"""Load StatsBomb events, lineups and 360 frames into DuckDB, with integrity checks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from touchline.db import DDL, connect
from touchline.fetch import OpenDataCache
from touchline.metrics.definitions import SET_PIECE_PASS_TYPES
from touchline.reception import assess_reception
from touchline.schema import Event, Frame, FramePlayer, LineupPlayer, Match, TeamLineup

# Clock time at which each period starts. Period 5 is a penalty shootout and is not
# open play, so it counts neither towards minutes nor towards the score.
_PERIOD_START_SECONDS = {1: 0, 2: 45 * 60, 3: 90 * 60, 4: 105 * 60}
_LAST_PLAYED_PERIOD = 4

_RECEIPT = "Ball Receipt*"
_SENDING_OFF_CARDS = frozenset({"Red Card", "Second Yellow"})


@dataclass(frozen=True)
class IngestSummary:
    matches: int
    events: int
    frames: int
    receptions: int


class IntegrityError(RuntimeError):
    """Raised when ingested data contradicts itself. Metrics must not run on it."""


def _clock_seconds(clock: str) -> int:
    minutes, seconds = clock.split(":")
    return int(minutes) * 60 + int(seconds)


def _event_row(match_id: int, event: Event) -> dict[str, Any]:
    pass_ = event.pass_
    carry = event.carry
    shot = event.shot
    return {
        "event_id": event.id,
        "match_id": match_id,
        "idx": event.index,
        "period": event.period,
        "minute": event.minute,
        "second": event.second,
        "type": event.type.name,
        "possession": event.possession,
        "possession_team": event.possession_team.name,
        "play_pattern": event.play_pattern.name,
        "team": event.team.name,
        "player_id": event.player.id if event.player else None,
        "player": event.player.name if event.player else None,
        "position": event.position.name if event.position else None,
        "x": event.location[0] if event.location else None,
        "y": event.location[1] if event.location else None,
        "under_pressure": event.under_pressure,
        "pass_recipient_id": pass_.recipient.id if pass_ and pass_.recipient else None,
        "pass_recipient": pass_.recipient.name if pass_ and pass_.recipient else None,
        "pass_length": pass_.length if pass_ else None,
        "pass_end_x": pass_.end_location[0] if pass_ else None,
        "pass_end_y": pass_.end_location[1] if pass_ else None,
        "pass_height": pass_.height.name if pass_ and pass_.height else None,
        "pass_type": pass_.type.name if pass_ and pass_.type else None,
        "pass_outcome": pass_.outcome.name if pass_ and pass_.outcome else None,
        "pass_cross": pass_.cross if pass_ else None,
        "pass_through_ball": pass_.through_ball if pass_ else None,
        "pass_shot_assist": pass_.shot_assist if pass_ else None,
        "pass_goal_assist": pass_.goal_assist if pass_ else None,
        "carry_end_x": carry.end_location[0] if carry else None,
        "carry_end_y": carry.end_location[1] if carry else None,
        "shot_xg": shot.statsbomb_xg if shot else None,
        "shot_outcome": shot.outcome.name if shot else None,
        "shot_type": shot.type.name if shot else None,
        "shot_body_part": shot.body_part.name if shot and shot.body_part else None,
        "shot_end_x": shot.end_location[0] if shot else None,
        "shot_end_y": shot.end_location[1] if shot else None,
    }


def _sent_off_at(player: LineupPlayer) -> tuple[int, int] | None:
    """Return (period, clock seconds) of a sending-off, if there was one."""
    for card in player.cards:
        if card.card_type in _SENDING_OFF_CARDS:
            return card.period, _clock_seconds(card.time)
    return None


def _period_end_seconds(events: list[Event]) -> dict[int, int]:
    ends: dict[int, int] = defaultdict(int)
    for event in events:
        clock = event.minute * 60 + event.second
        ends[event.period] = max(ends[event.period], clock)
    return ends


def _minutes_rows(
    match_id: int, lineups: list[TeamLineup], period_ends: dict[int, int]
) -> list[dict[str, Any]]:
    """Convert position stints to minutes, walking each period a stint overlaps.

    A stint from "00:00" in period 1 to "45:00" in period 2 means the player was in
    that position until the second half kicked off, so period 1 runs to its own end
    (including stoppage time) and period 2 contributes nothing.

    A sending-off caps the minutes. The lineup files can list a position stint that
    continues after the card, so the card time overrides the stint.
    """
    rows: list[dict[str, Any]] = []
    played = [period for period in period_ends if period <= _LAST_PLAYED_PERIOD]
    last_period = max(played)
    for team in lineups:
        for player in team.lineup:
            per_position: dict[str, float] = defaultdict(float)
            sent_off = _sent_off_at(player)
            for stint in player.positions:
                to_period = stint.to_period or last_period
                for period in range(stint.from_period, min(to_period, last_period) + 1):
                    start = (
                        _clock_seconds(stint.from_)
                        if period == stint.from_period
                        else _PERIOD_START_SECONDS[period]
                    )
                    end = (
                        _clock_seconds(stint.to)
                        if period == to_period and stint.to is not None
                        else period_ends[period]
                    )
                    if sent_off is not None:
                        off_period, off_clock = sent_off
                        if period > off_period:
                            continue
                        if period == off_period:
                            end = min(end, off_clock)
                    per_position[stint.position] += max(0, end - start) / 60
            rows.extend(
                {
                    "match_id": match_id,
                    "team": team.team_name,
                    "player_id": player.player_id,
                    "position": position,
                    "minutes": minutes,
                }
                for position, minutes in per_position.items()
                if minutes > 0
            )
    return rows


def _reception_rows(
    match_id: int, events: list[Event], frames: dict[str, Frame]
) -> list[dict[str, Any]]:
    by_id = {event.id: event for event in events}
    rows: list[dict[str, Any]] = []
    for event in events:
        if event.type.name != _RECEIPT or event.player is None or event.location is None:
            continue
        # A receipt with an outcome ("Incomplete") means the pass never arrived.
        if event.ball_receipt is not None and event.ball_receipt.outcome is not None:
            continue
        passes = [
            by_id[related]
            for related in event.related_events
            if related in by_id and by_id[related].type.name == "Pass"
        ]
        set_piece = any(
            item.pass_ is not None
            and item.pass_.type is not None
            and item.pass_.type.name in SET_PIECE_PASS_TYPES
            for item in passes
        )
        frame = frames.get(event.id)
        players: list[FramePlayer] | None = list(frame.freeze_frame) if frame else None
        context = assess_reception(
            (event.location[0], event.location[1]),
            players,
            frame.visible_area if frame else [],
        )
        rows.append(
            {
                "event_id": event.id,
                "match_id": match_id,
                "team": event.team.name,
                "player_id": event.player.id,
                "period": event.period,
                "minute": event.minute,
                "x": event.location[0],
                "y": event.location[1],
                "open_play": not set_piece,
                "frame_status": context.status.value,
                "space_yards": context.space_yards,
                "space_censored": context.space_censored,
                "visible_outfield_opponents": context.visible_outfield_opponents,
                "between_lines": context.between_lines,
            }
        )
    return rows


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = [row[0] for row in con.execute(f"DESCRIBE {table}").fetchall()]
    # Reorder by name so a change in row-dict order cannot silently shift values across columns.
    con.register("_rows", pd.DataFrame(rows)[columns])
    try:
        con.execute(f"INSERT INTO {table} SELECT * FROM _rows")
    finally:
        con.unregister("_rows")


def check_integrity(con: duckdb.DuckDBPyConnection, match_id: int) -> list[str]:
    """Return every way one ingested match contradicts itself. Empty means consistent."""
    problems: list[str] = []
    row = con.execute(
        "SELECT home_team, away_team, home_score, away_score FROM matches WHERE match_id = ?",
        [match_id],
    ).fetchone()
    if row is None:
        return [f"match {match_id} is not in the matches table"]
    home, away, home_score, away_score = row

    goals = dict(
        con.execute(
            """
            SELECT team, count(*) FROM events
            WHERE match_id = ? AND period <= ?
              AND ((type = 'Shot' AND shot_outcome = 'Goal') OR type = 'Own Goal For')
            GROUP BY team
            """,
            [match_id, _LAST_PLAYED_PERIOD],
        ).fetchall()
    )
    for team, expected in ((home, home_score), (away, away_score)):
        if goals.get(team, 0) != expected:
            problems.append(f"{team}: events give {goals.get(team, 0)} goals, result {expected}")

    checks = {
        "events with a location outside the pitch": """
            SELECT count(*) FROM events WHERE match_id = ?
            AND (x < 0 OR x > 120 OR y < 0 OR y > 80)""",
        "shots without xG": """
            SELECT count(*) FROM events WHERE match_id = ? AND type = 'Shot' AND shot_xg IS NULL""",
        "frames with no matching event": """
            SELECT count(*) FROM frames f ANTI JOIN events e USING (event_id)
            WHERE f.match_id = ?""",
        "receptions with no matching event": """
            SELECT count(*) FROM receptions r ANTI JOIN events e USING (event_id)
            WHERE r.match_id = ?""",
        "events by a player missing from the lineup": """
            SELECT count(*) FROM events e
            ANTI JOIN lineups l ON e.match_id = l.match_id AND e.player_id = l.player_id
            WHERE e.match_id = ? AND e.player_id IS NOT NULL""",
    }
    for label, sql in checks.items():
        result = con.execute(sql, [match_id]).fetchone()
        count = result[0] if result else 0
        if count:
            problems.append(f"{count} {label}")
    return problems


def ingest(
    cache: OpenDataCache,
    db_path: Path,
    competition_id: int,
    season_id: int,
    team: str | None,
) -> IngestSummary:
    """Rebuild the database from the cache. Fails if any match has integrity problems.

    ``team`` limits ingest to one team's matches; ``None`` takes the whole season.
    """
    matches = [Match.model_validate(item) for item in cache.matches(competition_id, season_id)]
    if team is not None:
        matches = [match for match in matches if team in match.teams]
    if not matches:
        raise ValueError(f"no matches for team {team!r}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path, read_only=False)
    try:
        con.execute(DDL)
        totals = {"events": 0, "frames": 0, "receptions": 0}
        for match in sorted(matches, key=lambda item: item.match_date):
            counts = _ingest_match(con, cache, match, competition_id, season_id)
            for key, value in counts.items():
                totals[key] += value

        problems = [
            f"match {match.match_id}: {problem}"
            for match in matches
            for problem in check_integrity(con, match.match_id)
        ]
        if problems:
            raise IntegrityError("; ".join(problems))
    finally:
        con.close()
    return IngestSummary(matches=len(matches), **totals)


def _ingest_match(
    con: duckdb.DuckDBPyConnection,
    cache: OpenDataCache,
    match: Match,
    competition_id: int,
    season_id: int,
) -> dict[str, int]:
    match_id = match.match_id
    events = [Event.model_validate(item) for item in cache.events(match_id)]
    lineups = [TeamLineup.model_validate(item) for item in cache.lineups(match_id)]
    frames = {
        frame.event_uuid: frame
        for frame in (Frame.model_validate(item) for item in cache.frames(match_id))
    }

    _insert(
        con,
        "matches",
        [
            {
                "match_id": match_id,
                "competition_id": competition_id,
                "season_id": season_id,
                "competition": match.competition.competition_name,
                "season": match.season.season_name,
                "match_date": match.match_date,
                "stage": match.competition_stage.name,
                "home_team": match.home_team.home_team_name,
                "away_team": match.away_team.away_team_name,
                "home_score": match.home_score,
                "away_score": match.away_score,
            }
        ],
    )
    _insert(
        con,
        "lineups",
        [
            {
                "match_id": match_id,
                "team": team.team_name,
                "player_id": player.player_id,
                "player": player.player_name,
                "jersey_number": player.jersey_number,
                "sent_off_period": sent_off[0] if sent_off else None,
                "sent_off_minute": sent_off[1] // 60 if sent_off else None,
                "sent_off_second": sent_off[1] % 60 if sent_off else None,
            }
            for team in lineups
            for player in team.lineup
            for sent_off in [_sent_off_at(player)]
        ],
    )
    _insert(con, "player_minutes", _minutes_rows(match_id, lineups, _period_end_seconds(events)))
    _insert(con, "events", [_event_row(match_id, event) for event in events])
    _insert(
        con,
        "frames",
        [
            {"event_id": frame.event_uuid, "match_id": match_id, "visible_area": frame.visible_area}
            for frame in frames.values()
        ],
    )
    _insert(
        con,
        "frame_players",
        [
            {
                "event_id": frame.event_uuid,
                "match_id": match_id,
                "teammate": player.teammate,
                "actor": player.actor,
                "keeper": player.keeper,
                "x": player.location[0],
                "y": player.location[1],
            }
            for frame in frames.values()
            for player in frame.freeze_frame
        ],
    )
    reception_rows = _reception_rows(match_id, events, frames)
    _insert(con, "receptions", reception_rows)
    return {"events": len(events), "frames": len(frames), "receptions": len(reception_rows)}
