"""Load StatsBomb events, lineups and 360 frames into DuckDB, with integrity checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from touchline.db import DDL, connect
from touchline.fetch import OpenDataCache
from touchline.metrics.definitions import SET_PIECE_PASS_TYPES
from touchline.minutes import PERIOD_START_SECONDS, minutes_from_events
from touchline.reception import assess_reception
from touchline.schema import Event, Frame, FramePlayer, Match, TeamLineup

# Clock time at which each period starts. Period 5 is a penalty shootout and is not
# open play, so it counts neither towards minutes nor towards the score.
_PERIOD_START_SECONDS = {1: 0, 2: 45 * 60, 3: 90 * 60, 4: 105 * 60}
_LAST_PLAYED_PERIOD = 4

_RECEIPT = "Ball Receipt*"
# Locations are recorded to 0.1 yards and can overshoot the line: Euro 2024 has a shot at
# x = 120.1. An overshoot this small is rounding, not a contradiction.
_PITCH_SLACK_YARDS = 1.0
_MINUTES_SLACK = 0.5


@dataclass(frozen=True)
class IngestSummary:
    matches: int
    events: int
    frames: int
    receptions: int


class IntegrityError(RuntimeError):
    """Raised when ingested data contradicts itself. Metrics must not run on it."""


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


def _match_length_minutes(con: duckdb.DuckDBPyConnection, match_id: int) -> float:
    rows = con.execute(
        "SELECT period, max(minute * 60 + second) FROM events "
        "WHERE match_id = ? AND period <= ? GROUP BY period",
        [match_id, _LAST_PLAYED_PERIOD],
    ).fetchall()
    seconds: int = sum(int(end) - PERIOD_START_SECONDS[int(period)] for period, end in rows)
    return seconds / 60


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
            AND (x < -$slack OR x > 120 + $slack OR y < -$slack OR y > 80 + $slack)""",
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
    length = _match_length_minutes(con, match_id)
    over = con.execute(
        "SELECT count(*) FROM (SELECT player_id FROM player_minutes WHERE match_id = ? "
        "GROUP BY player_id HAVING sum(minutes) > ?)",
        [match_id, length + _MINUTES_SLACK],
    ).fetchone()
    if over and over[0]:
        problems.append(f"{over[0]} players with more minutes than the match's {length:.1f}")
    team_totals = con.execute(
        "SELECT team, sum(minutes) FROM player_minutes WHERE match_id = ? GROUP BY team",
        [match_id],
    ).fetchall()
    for team, total in team_totals:
        if total > 11 * length + _MINUTES_SLACK:
            problems.append(f"{team}: {total:.1f} player-minutes in a {length:.1f}-minute match")

    for label, sql in checks.items():
        result = con.execute(sql.replace("$slack", str(_PITCH_SLACK_YARDS)), [match_id]).fetchone()
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
            }
            for team in lineups
            for player in team.lineup
        ],
    )
    minutes, sendings_off = minutes_from_events(events)
    _insert(con, "player_minutes", [{"match_id": match_id, **asdict(row)} for row in minutes])
    _insert(con, "sendings_off", [{"match_id": match_id, **asdict(row)} for row in sendings_off])
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
