"""Pass network for one team, up to its first change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from touchline.metrics._sql import COMPLETED_PASS, OPEN_PLAY_PASS, fetch_dicts, rounded
from touchline.metrics.team import UnknownEntityError, match_row


@dataclass(frozen=True)
class PassNetwork:
    match_id: int
    team: str
    window_end_minute: int | None
    nodes: list[dict[str, Any]]
    links: list[dict[str, Any]]


def _first_change(
    con: duckdb.DuckDBPyConnection, match_id: int, team: str
) -> tuple[int, int, int] | None:
    """When the team's eleven first changed: a substitution or a sending-off.

    "Player Off" events are not used: they also mark players leaving for treatment and
    coming back, which does not change the team's shape for long.
    """
    row = con.execute(
        """
        SELECT period, minute, second FROM (
            SELECT period, minute, second FROM events
            WHERE match_id = $match AND team = $team AND type = 'Substitution' AND period <= 4
            UNION ALL
            SELECT period, minute, second FROM sendings_off
            WHERE match_id = $match AND team = $team
        )
        ORDER BY period, minute, second LIMIT 1
        """,
        {"match": match_id, "team": team},
    ).fetchone()
    return None if row is None else (int(row[0]), int(row[1]), int(row[2]))


def pass_network(con: duckdb.DuckDBPyConnection, match_id: int, team: str) -> PassNetwork:
    """Nodes and undirected links of completed open-play passes before the first change."""
    match = match_row(con, match_id)
    if team not in (match["home_team"], match["away_team"]):
        raise UnknownEntityError(f"{team} did not play in match {match_id}")
    change = _first_change(con, match_id, team)
    # With no change at all, the sentinel period keeps the comparison true for every event.
    period, minute, second = change if change is not None else (99, 0, 0)
    params = {"match": match_id, "team": team, "p": period, "m": minute, "s": second}

    passes = f"""
        SELECT player_id, player, pass_recipient_id, pass_recipient, x, y, pass_end_x, pass_end_y
        FROM events
        WHERE match_id = $match AND team = $team AND {COMPLETED_PASS}
          AND {OPEN_PLAY_PASS} AND pass_recipient_id IS NOT NULL AND period <= 4
          AND (period, minute, second) < ($p, $m, $s)
    """
    nodes = fetch_dicts(
        con,
        f"""
        WITH passes AS ({passes}),
        touches AS (
            SELECT player_id, player, x, y FROM passes
            UNION ALL
            SELECT pass_recipient_id, pass_recipient, pass_end_x, pass_end_y FROM passes
        )
        SELECT t.player_id, t.player, l.jersey_number,
               avg(t.x) AS avg_x, avg(t.y) AS avg_y, count(*) AS touches
        FROM touches t
        JOIN lineups l ON l.match_id = $match AND l.team = $team AND l.player_id = t.player_id
        GROUP BY ALL ORDER BY touches DESC
        """,
        params,
    )
    links = fetch_dicts(
        con,
        f"""
        WITH passes AS ({passes})
        SELECT least(player_id, pass_recipient_id) AS player_a,
               greatest(player_id, pass_recipient_id) AS player_b,
               count(*) AS passes
        FROM passes GROUP BY 1, 2 ORDER BY passes DESC, 1, 2
        """,
        params,
    )
    for node in nodes:
        node["avg_x"] = rounded(node["avg_x"], 1)
        node["avg_y"] = rounded(node["avg_y"], 1)
    return PassNetwork(
        match_id=match_id,
        team=team,
        window_end_minute=None if change is None else change[1],
        nodes=nodes,
        links=links,
    )


def player_pass_links(
    con: duckdb.DuckDBPyConnection, match_id: int, player_id: int
) -> dict[str, Any]:
    """Who a player exchanged the most completed open-play passes with, in a whole match."""
    match_row(con, match_id)
    links = fetch_dicts(
        con,
        f"""
        WITH passes AS (
            SELECT player_id, pass_recipient_id FROM events
            WHERE match_id = ? AND {COMPLETED_PASS} AND {OPEN_PLAY_PASS}
              AND pass_recipient_id IS NOT NULL AND period <= 4
              AND (player_id = ? OR pass_recipient_id = ?)
        ),
        counted AS (
            SELECT CASE WHEN player_id = ? THEN pass_recipient_id ELSE player_id END AS partner_id,
                   count(*) FILTER (player_id = ?) AS passes_to_partner,
                   count(*) FILTER (pass_recipient_id = ?) AS passes_from_partner
            FROM passes GROUP BY 1
        )
        SELECT l.player AS partner, c.passes_to_partner, c.passes_from_partner
        FROM counted c JOIN lineups l ON l.match_id = ? AND l.player_id = c.partner_id
        ORDER BY c.passes_to_partner + c.passes_from_partner DESC, l.player
        """,
        [match_id, player_id, player_id, player_id, player_id, player_id, match_id],
    )
    return {"match_id": match_id, "player_id": player_id, "partners": links}
