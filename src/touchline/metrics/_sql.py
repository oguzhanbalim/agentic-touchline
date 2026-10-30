"""SQL fragments and small query helpers shared by the metric modules."""

from __future__ import annotations

from typing import Any

import duckdb

from touchline.config import GOAL_CENTRE
from touchline.metrics.definitions import (
    PROGRESSIVE_MIN_ABSOLUTE_GAIN_YARDS,
    PROGRESSIVE_MIN_RELATIVE_GAIN,
    SET_PIECE_PASS_TYPES,
)

_GOAL_X, _GOAL_Y = GOAL_CENTRE
_SET_PIECES_SQL = ", ".join(f"'{name}'" for name in sorted(SET_PIECE_PASS_TYPES))

OPEN_PLAY_PASS = f"(pass_type IS NULL OR pass_type NOT IN ({_SET_PIECES_SQL}))"
COMPLETED_PASS = "(type = 'Pass' AND pass_outcome IS NULL)"
# Shootout penalties are period 5 and must not inflate shots, goals or xG.
IN_PLAY = "period <= 4"


def _distance_to_goal(x: str, y: str) -> str:
    return f"sqrt(power({_GOAL_X} - {x}, 2) + power({_GOAL_Y} - {y}, 2))"


def _progressive(end_x: str, end_y: str) -> str:
    start = _distance_to_goal("x", "y")
    end = _distance_to_goal(end_x, end_y)
    return (
        f"({end} <= {1 - PROGRESSIVE_MIN_RELATIVE_GAIN} * {start} "
        f"AND {start} - {end} >= {PROGRESSIVE_MIN_ABSOLUTE_GAIN_YARDS})"
    )


PROGRESSIVE_PASS = (
    f"({COMPLETED_PASS} AND {OPEN_PLAY_PASS} AND " + _progressive("pass_end_x", "pass_end_y") + ")"
)
PROGRESSIVE_CARRY = "(type = 'Carry' AND " + _progressive("carry_end_x", "carry_end_y") + ")"


def fetch_dicts(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: list[Any] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a parameterised query and return rows as dictionaries."""
    cursor = con.execute(sql, params if params is not None else [])
    columns = [column[0] for column in cursor.description or []]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def per_90(count: float, minutes: float) -> float | None:
    return round(count * 90 / minutes, 2) if minutes > 0 else None


def rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)
