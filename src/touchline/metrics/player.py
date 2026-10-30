"""Player-level metrics, reception zones and role comparison."""

from __future__ import annotations

import statistics
from typing import Any

import duckdb

from touchline.metrics._sql import (
    COMPLETED_PASS,
    IN_PLAY,
    OPEN_PLAY_PASS,
    PROGRESSIVE_CARRY,
    PROGRESSIVE_PASS,
    fetch_dicts,
    per_90,
    rounded,
)
from touchline.metrics.definitions import DEFAULT_PEER_MIN_MINUTES, MIN_SAMPLE_FOR_MEDIAN
from touchline.metrics.team import UnknownEntityError

# StatsBomb's position labels grouped into roles. Every label in the Events specification
# belongs to exactly one group; test_metrics.py enforces that.
ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    "goalkeeper": ("Goalkeeper",),
    "centre_back": ("Center Back", "Left Center Back", "Right Center Back"),
    "full_back": ("Left Back", "Right Back", "Left Wing Back", "Right Wing Back"),
    "defensive_midfield": (
        "Center Defensive Midfield",
        "Left Defensive Midfield",
        "Right Defensive Midfield",
    ),
    "central_midfield": (
        "Center Midfield",
        "Left Center Midfield",
        "Right Center Midfield",
    ),
    "wide_midfield": ("Left Midfield", "Right Midfield"),
    "attacking_midfield": (
        "Center Attacking Midfield",
        "Left Attacking Midfield",
        "Right Attacking Midfield",
    ),
    "winger": ("Left Wing", "Right Wing"),
    "striker": (
        "Center Forward",
        "Left Center Forward",
        "Right Center Forward",
        "Secondary Striker",
    ),
}
_ROLE_BY_POSITION = {
    position: role for role, positions in ROLE_GROUPS.items() for position in positions
}

_OPEN_RECEPTION = "open_play"

_COMPARED_COUNTS: tuple[str, ...] = (
    "shots",
    "xg",
    "passes_completed",
    "progressive_passes",
    "progressive_carries",
    "pressures",
    "shot_assists",
)


def _scope(match_id: int | None) -> tuple[str, list[Any]]:
    return ("AND match_id = ?", [match_id]) if match_id is not None else ("", [])


def _player_row(con: duckdb.DuckDBPyConnection, player_id: int) -> dict[str, Any]:
    rows = fetch_dicts(
        con,
        "SELECT player_id, any_value(player) AS player, any_value(team) AS team "
        "FROM lineups WHERE player_id = ? GROUP BY player_id",
        [player_id],
    )
    if not rows:
        raise UnknownEntityError(f"player {player_id} is not in the database")
    return rows[0]


def find_players(con: duckdb.DuckDBPyConnection, query: str) -> list[dict[str, Any]]:
    """Match a name fragment, ignoring case and diacritics, Turkish letters included."""
    needle = fold_name(query)
    rows = fetch_dicts(
        con,
        """
        SELECT l.player_id, any_value(l.player) AS player, any_value(l.team) AS team,
               round(coalesce(sum(m.minutes), 0), 1) AS minutes
        FROM lineups l
        LEFT JOIN player_minutes m
          ON m.match_id = l.match_id AND m.player_id = l.player_id
        GROUP BY l.player_id ORDER BY minutes DESC, player
        """,
    )
    return [row for row in rows if needle and needle in fold_name(row["player"])]


_FOLD = str.maketrans(
    {
        "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ç": "c", "Ç": "c",
        "ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "á": "a", "é": "e", "í": "i", "ó": "o",
        "ú": "u", "ñ": "n", "à": "a", "è": "e", "ã": "a", "õ": "o", "ê": "e", "â": "a",
        "ä": "a", "ë": "e", "ï": "i", "ß": "ss", "ø": "o", "ć": "c", "č": "c", "š": "s",
        "ž": "z", "ř": "r", "ě": "e", "ý": "y", "ů": "u", "ń": "n", "ł": "l",
    }
)  # fmt: skip


def fold_name(name: str) -> str:
    return " ".join(name.translate(_FOLD).lower().split())


def player_metrics(
    con: duckdb.DuckDBPyConnection, player_id: int, match_id: int | None = None
) -> dict[str, Any]:
    """All metrics for one player, in one match or across every ingested match."""
    player = _player_row(con, player_id)
    scope_sql, scope_params = _scope(match_id)
    minutes = fetch_dicts(
        con,
        f"""
        SELECT position, round(sum(minutes), 1) AS minutes FROM player_minutes
        WHERE player_id = ? {scope_sql} GROUP BY position ORDER BY minutes DESC
        """,
        [player_id, *scope_params],
    )
    total_minutes = round(sum(row["minutes"] for row in minutes), 1)
    matches = fetch_dicts(
        con,
        f"""
        SELECT count(DISTINCT match_id) AS matches_played FROM player_minutes
        WHERE player_id = ? AND minutes > 0 {scope_sql}
        """,
        [player_id, *scope_params],
    )[0]["matches_played"]
    counts = fetch_dicts(
        con,
        f"""
        SELECT
            count(*) FILTER (type = 'Shot') AS shots,
            count(*) FILTER (type = 'Shot' AND shot_outcome = 'Goal') AS goals,
            coalesce(sum(shot_xg), 0) AS xg,
            count(*) FILTER (type = 'Pass') AS passes_attempted,
            count(*) FILTER ({COMPLETED_PASS}) AS passes_completed,
            count(*) FILTER (type = 'Pass' AND {OPEN_PLAY_PASS}) AS open_play_passes_attempted,
            count(*) FILTER ({COMPLETED_PASS} AND {OPEN_PLAY_PASS}) AS open_play_passes_completed,
            count(*) FILTER ({PROGRESSIVE_PASS}) AS progressive_passes,
            count(*) FILTER (type = 'Pass' AND pass_shot_assist) AS shot_assists,
            count(*) FILTER (type = 'Pass' AND pass_goal_assist) AS goal_assists,
            count(*) FILTER (type = 'Carry') AS carries,
            count(*) FILTER ({PROGRESSIVE_CARRY}) AS progressive_carries,
            count(*) FILTER (type = 'Pressure') AS pressures,
            count(*) FILTER (type = 'Pressure' AND x < 40) AS pressures_defensive_third,
            count(*) FILTER (type = 'Pressure' AND x >= 40 AND x < 80) AS pressures_middle_third,
            count(*) FILTER (type = 'Pressure' AND x >= 80) AS pressures_attacking_third
        FROM events
        WHERE player_id = ? AND {IN_PLAY} {scope_sql}
        """,
        [player_id, *scope_params],
    )[0]
    attempted = counts["passes_attempted"]
    completion = rounded(100 * counts["passes_completed"] / attempted, 1) if attempted else None
    per_90s = {key: per_90(counts[key], total_minutes) for key in _COMPARED_COUNTS}
    counts["xg"] = rounded(counts["xg"])
    return {
        **player,
        "match_id": match_id,
        "matches_played": matches,
        "minutes": total_minutes,
        "positions": minutes,
        **counts,
        "pass_completion_pct": completion,
        "per_90": per_90s,
        "receptions": _reception_summary(con, player_id, match_id),
    }


def _reception_summary(
    con: duckdb.DuckDBPyConnection, player_id: int, match_id: int | None
) -> dict[str, Any]:
    scope_sql, scope_params = _scope(match_id)
    row = fetch_dicts(
        con,
        f"""
        SELECT
            count(*) AS open_play_receptions,
            count(*) FILTER (frame_status = 'ok') AS with_usable_frame,
            count(*) FILTER (space_yards IS NOT NULL) AS space_measured,
            count(*) FILTER (space_censored) AS space_censored,
            median(space_yards) AS median_space_yards,
            count(*) FILTER (between_lines IS NOT NULL) AS between_lines_assessable,
            count(*) FILTER (between_lines) AS between_lines
        FROM receptions
        WHERE player_id = ? AND {_OPEN_RECEPTION} {scope_sql}
        """,
        [player_id, *scope_params],
    )[0]
    row["median_space_yards"] = rounded(row["median_space_yards"], 1)
    assessable = row["between_lines_assessable"]
    row["between_lines_pct"] = (
        rounded(100 * row["between_lines"] / assessable, 1) if assessable else None
    )
    return row


def player_shots(
    con: duckdb.DuckDBPyConnection, player_id: int, match_id: int | None = None
) -> dict[str, Any]:
    """Each of a player's shots. Derived fields only, so no event ids or raw coordinates."""
    player = _player_row(con, player_id)
    scope_sql, scope_params = _scope(match_id)
    shots = fetch_dicts(
        con,
        f"""
        SELECT match_id, minute, shot_outcome AS outcome, shot_type AS type,
               shot_body_part AS body_part, round(shot_xg, 2) AS xg
        FROM events
        WHERE player_id = ? AND type = 'Shot' AND {IN_PLAY} {scope_sql}
        ORDER BY match_id, period, minute, second
        """,
        [player_id, *scope_params],
    )
    return {**player, "match_id": match_id, "shots": shots}


# Zones. Channels follow the pitch markings: penalty area at y = 18 and 62, six-yard box
# at y = 30 and 50. Low y is the attacking team's left.
def _zone_columns(x: str = "x", y: str = "y") -> str:
    return f"""
        CASE WHEN {x} < 40 THEN 'defensive_third' WHEN {x} < 80 THEN 'middle_third'
             ELSE 'final_third' END AS third,
        CASE WHEN {y} < 18 THEN 'left_wing' WHEN {y} < 30 THEN 'left_half_space'
             WHEN {y} < 50 THEN 'centre' WHEN {y} < 62 THEN 'right_half_space'
             ELSE 'right_wing' END AS channel
    """


def _zone_rows(
    con: duckdb.DuckDBPyConnection, where: str, params: list[Any]
) -> list[dict[str, Any]]:
    return fetch_dicts(
        con,
        f"""
        SELECT {_zone_columns()},
               count(*) AS receptions,
               count(space_yards) AS space_measured,
               median(space_yards) AS median_space_yards,
               count(*) FILTER (between_lines) AS between_lines
        FROM receptions WHERE {_OPEN_RECEPTION} AND {where}
        GROUP BY 1, 2
        """,
        params,
    )


def _reliable_median(row: dict[str, Any]) -> float | None:
    """Only report a median once enough measured frames sit behind it."""
    if row["space_measured"] < MIN_SAMPLE_FOR_MEDIAN:
        return None
    return rounded(row["median_space_yards"], 1)


def primary_role(
    con: duckdb.DuckDBPyConnection, player_id: int, match_id: int | None = None
) -> str | None:
    """The role group holding most of a player's minutes in the given scope."""
    scope_sql, scope_params = _scope(match_id)
    rows = fetch_dicts(
        con,
        f"""
        SELECT position, sum(minutes) AS minutes FROM player_minutes
        WHERE player_id = ? {scope_sql} GROUP BY position
        """,
        [player_id, *scope_params],
    )
    totals: dict[str, float] = {}
    for row in rows:
        role = _ROLE_BY_POSITION.get(row["position"])
        if role is not None:
            totals[role] = totals.get(role, 0.0) + row["minutes"]
    return max(totals, key=lambda key: totals[key]) if totals else None


def reception_zones(
    con: duckdb.DuckDBPyConnection, player_id: int, match_id: int | None = None
) -> dict[str, Any]:
    """Where a player received the ball, and how role peers' receptions were distributed."""
    player = _player_row(con, player_id)
    scope_sql, scope_params = _scope(match_id)
    own = _zone_rows(con, f"player_id = ? {scope_sql}", [player_id, *scope_params])
    role = primary_role(con, player_id, match_id)
    peer_rows = _peer_zone_rows(con, player_id, role) if role else []

    own_total = sum(row["receptions"] for row in own)
    peer_total = sum(row["receptions"] for row in peer_rows)
    peer_lookup = {(row["third"], row["channel"]): row for row in peer_rows}
    zones = []
    for row in own:
        peer = peer_lookup.pop((row["third"], row["channel"]), None)
        zones.append(_zone_entry(row, peer, own_total, peer_total))
    zones.extend(_zone_entry(None, peer, own_total, peer_total) for peer in peer_lookup.values())
    zones.sort(key=lambda zone: (-zone["receptions"], -zone["peer_share_pct"], zone["zone"]))
    return {
        **player,
        "match_id": match_id,
        "role": role,
        "open_play_receptions": own_total,
        "peer_receptions": peer_total,
        "zones": zones,
    }


def _zone_entry(
    own: dict[str, Any] | None,
    peer: dict[str, Any] | None,
    own_total: int,
    peer_total: int,
) -> dict[str, Any]:
    source = own or peer
    assert source is not None
    receptions = own["receptions"] if own else 0
    peer_receptions = peer["receptions"] if peer else 0
    return {
        "zone": f"{source['third']}:{source['channel']}",
        "receptions": receptions,
        "share_pct": rounded(100 * receptions / own_total, 1) if own_total else 0.0,
        "median_space_yards": _reliable_median(own) if own else None,
        "between_lines": own["between_lines"] if own else 0,
        "peer_share_pct": rounded(100 * peer_receptions / peer_total, 1) if peer_total else 0.0,
        "peer_median_space_yards": _reliable_median(peer) if peer else None,
    }


def _peer_zone_rows(
    con: duckdb.DuckDBPyConnection, player_id: int, role: str
) -> list[dict[str, Any]]:
    peers = _peer_ids(con, player_id, role, DEFAULT_PEER_MIN_MINUTES)
    if not peers:
        return []
    return fetch_dicts(
        con,
        f"""
        SELECT {_zone_columns("r.x", "r.y")},
               count(*) AS receptions, count(r.space_yards) AS space_measured,
               median(r.space_yards) AS median_space_yards
        FROM receptions r
        JOIN events e USING (event_id)
        WHERE r.open_play AND r.player_id IN (SELECT unnest(?))
          AND list_contains(?, e.position)
        GROUP BY 1, 2
        """,
        [peers, list(ROLE_GROUPS[role])],
    )


def _peer_ids(
    con: duckdb.DuckDBPyConnection, player_id: int, role: str, min_minutes: float
) -> list[int]:
    rows = con.execute(
        """
        SELECT player_id FROM player_minutes
        WHERE list_contains(?, position) AND player_id <> ?
        GROUP BY player_id HAVING sum(minutes) >= ?
        """,
        [list(ROLE_GROUPS[role]), player_id, min_minutes],
    ).fetchall()
    return [row[0] for row in rows]


def _role_values(
    con: duckdb.DuckDBPyConnection, role: str, match_id: int | None, player_ids: list[int]
) -> dict[int, dict[str, float | None]]:
    """Per-90 and reception rates for each player, counting only time spent in this role."""
    positions = list(ROLE_GROUPS[role])
    scope_sql, scope_params = _scope(match_id)
    minutes = dict(
        con.execute(
            f"""
            SELECT player_id, sum(minutes) FROM player_minutes
            WHERE list_contains(?, position) AND player_id IN (SELECT unnest(?)) {scope_sql}
            GROUP BY player_id
            """,
            [positions, player_ids, *scope_params],
        ).fetchall()
    )
    counts = fetch_dicts(
        con,
        f"""
        SELECT player_id,
            count(*) FILTER (type = 'Shot') AS shots,
            coalesce(sum(shot_xg), 0) AS xg,
            count(*) FILTER ({COMPLETED_PASS}) AS passes_completed,
            count(*) FILTER ({PROGRESSIVE_PASS}) AS progressive_passes,
            count(*) FILTER ({PROGRESSIVE_CARRY}) AS progressive_carries,
            count(*) FILTER (type = 'Pressure') AS pressures,
            count(*) FILTER (type = 'Pass' AND pass_shot_assist) AS shot_assists
        FROM events
        WHERE list_contains(?, position) AND player_id IN (SELECT unnest(?)) AND {IN_PLAY}
          {scope_sql}
        GROUP BY player_id
        """,
        [positions, player_ids, *scope_params],
    )
    receptions = fetch_dicts(
        con,
        f"""
        SELECT r.player_id,
            median(r.space_yards) AS median_space_yards,
            count(r.space_yards) AS space_measured,
            count(*) FILTER (r.between_lines IS NOT NULL) AS assessable,
            count(*) FILTER (r.between_lines) AS between_lines
        FROM receptions r JOIN events e USING (event_id)
        WHERE r.open_play AND list_contains(?, e.position)
          AND r.player_id IN (SELECT unnest(?)) {scope_sql.replace("match_id", "r.match_id")}
        GROUP BY r.player_id
        """,
        [positions, player_ids, *scope_params],
    )
    by_player = {row["player_id"]: row for row in counts}
    reception_by_player = {row["player_id"]: row for row in receptions}
    values: dict[int, dict[str, float | None]] = {}
    for player_id, played in minutes.items():
        if played <= 0:
            continue
        row = by_player.get(player_id, {})
        entry: dict[str, float | None] = {
            key: per_90(row.get(key, 0), played) for key in _COMPARED_COUNTS
        }
        rec = reception_by_player.get(player_id)
        if rec and rec["space_measured"] >= MIN_SAMPLE_FOR_MEDIAN:
            entry["median_space_yards"] = rounded(rec["median_space_yards"], 1)
        else:
            entry["median_space_yards"] = None
        if rec and rec["assessable"] >= MIN_SAMPLE_FOR_MEDIAN:
            entry["between_lines_pct"] = rounded(100 * rec["between_lines"] / rec["assessable"], 1)
        else:
            entry["between_lines_pct"] = None
        values[player_id] = entry
    return values


def role_comparison(
    con: duckdb.DuckDBPyConnection,
    player_id: int,
    match_id: int | None = None,
    min_minutes: float = DEFAULT_PEER_MIN_MINUTES,
) -> dict[str, Any]:
    """Compare a player with role peers. Peers always use all ingested matches."""
    player = _player_row(con, player_id)
    role = primary_role(con, player_id, match_id)
    if role is None:
        return {**player, "match_id": match_id, "role": None, "peers": 0, "metrics": []}

    own = _role_values(con, role, match_id, [player_id]).get(player_id, {})
    peer_ids = _peer_ids(con, player_id, role, min_minutes)
    peers = _role_values(con, role, None, peer_ids) if peer_ids else {}

    metrics = []
    for key in (*_COMPARED_COUNTS, "median_space_yards", "between_lines_pct"):
        value = own.get(key)
        peer_values = [entry[key] for entry in peers.values() if entry.get(key) is not None]
        comparable = [item for item in peer_values if item is not None]
        rank = None
        if value is not None and comparable:
            rank = 1 + sum(item > value for item in comparable)
        metrics.append(
            {
                "metric": key,
                "unit": _unit(key),
                "player_value": value,
                "peer_median": rounded(statistics.median(comparable), 2) if comparable else None,
                "peers_measured": len(comparable),
                "rank_including_player": rank,
            }
        )
    return {
        **player,
        "match_id": match_id,
        "role": role,
        "peers": len(peers),
        "peer_min_minutes": min_minutes,
        "metrics": metrics,
    }


def _unit(key: str) -> str:
    if key == "median_space_yards":
        return "yards"
    if key == "between_lines_pct":
        return "percent of assessable receptions"
    return "per 90 minutes in this role"
