"""A smoke test against the real API. Never run by default, and not yet run at all.

Run with `pytest -m live` once ANTHROPIC_API_KEY is set and `touchline ingest` has run.
It needs a real database, costs money, and only asserts the shape of what comes back.
"""

from __future__ import annotations

import os

import anthropic
import pytest

from touchline.agents.coach import CoachSession
from touchline.config import default_paths
from touchline.db import connect
from touchline.metrics.player import find_players
from touchline.tools import ToolBox

pytestmark = pytest.mark.live


@pytest.mark.skipif("ANTHROPIC_API_KEY" not in os.environ, reason="needs ANTHROPIC_API_KEY")
@pytest.mark.parametrize(
    "question",
    [
        "Where should I receive the ball?",
        "¿Dónde debo recibir el balón?",
        "Topu nerede almalıyım?",
    ],
)
def test_coach_answers_with_verified_numbers(question: str) -> None:
    con = connect(default_paths().db_path)
    player = find_players(con, "arda guler")[0]
    session = CoachSession(
        anthropic.Anthropic(), ToolBox(con), player["player_id"], player["player"]
    )
    reply = session.ask(question)
    con.close()
    assert reply.outcome == "answered"
    assert reply.tool_calls_made > 0
    assert reply.faithfulness is not None
    assert reply.faithfulness.passed
