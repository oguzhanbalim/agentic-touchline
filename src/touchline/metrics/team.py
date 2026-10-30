"""Match- and team-level metrics."""

from __future__ import annotations

from typing import Any

import duckdb

from touchline.metrics._sql import (
    COMPLETED_PASS,
    IN_PLAY,
    PROGRESSIVE_CARRY,
    PROGRESSIVE_PASS,
    fetch_dicts,
    rounded,
)


class UnknownEntityError(LookupError):
    """A match or player id was not found. The message is safe to show the model."""


def match_row(con: duckdb.DuckDBPyConnection, match_id: int) -> dict[str, Any]:
    rows = fetch_dicts(
        con,
        """
        SELECT match_id, CAST(match_date AS VARCHAR) AS match_date, competition, season, stage,
               home_team, away_team, home_score, away_score
        FROM matches WHERE match_id = ?
        """,
        [match_id],
    )
    if not rows:
        raise UnknownEntityError(f"match {match_id} is not in the database")
    return rows[0]


def match_summary(con: duckdb.DuckDBPyConnection, match_id: int) -> dict[str, Any]:
    """Score and team totals for one match, one row per team."""
    match = match_row(con, match_id)
    teams = fetch_dicts(
        con,
        f"""
        SELECT team,
            count(*) FILTER (type = 'Shot') AS shots,
            count(*) FILTER (type = 'Shot' AND shot_outcome = 'Goal') AS goals_from_shots,
            coalesce(sum(shot_xg), 0) AS xg,
            count(*) FILTER (type = 'Pass') AS passes_attempted,
            count(*) FILTER ({COMPLETED_PASS}) AS passes_completed,
            count(*) FILTER ({PROGRESSIVE_PASS}) AS progressive_passes,
            count(*) FILTER ({PROGRESSIVE_CARRY}) AS progressive_carries,
            count(*) FILTER (type = 'Pressure') AS pressures,
            count(*) FILTER (type = 'Pressure' AND x >= 80) AS pressures_attacking_third,
            count(*) FILTER (type = 'Own Goal For') AS own_goals_received
        FROM events
        WHERE match_id = ? AND {IN_PLAY}
        GROUP BY team ORDER BY team
        """,
        [match_id],
    )
    for team in teams:
        team["xg"] = rounded(team["xg"])
        attempted = team["passes_attempted"]
        team["pass_completion_pct"] = (
            rounded(100 * team["passes_completed"] / attempted, 1) if attempted else None
        )
    return {**match, "teams": teams}


def list_matches(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return fetch_dicts(
        con,
        """
        SELECT match_id, CAST(match_date AS VARCHAR) AS match_date, stage,
               home_team, away_team, home_score, away_score
        FROM matches ORDER BY match_date
        """,
    )
