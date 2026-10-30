"""The analyst and coach, end to end against scripted model turns and a real database."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fakes import ScriptedClient, message, refusal, text, tool_use
from synthetic import ALPHA, FORWARD_A, MATCH_ID
from touchline.agents.analyst import REPORT_SCHEMA, run_match_analyst
from touchline.agents.coach import CoachSession
from touchline.agents.prompts import ANALYST_SYSTEM, COACH_SYSTEM
from touchline.checks.routing import Route
from touchline.report import (
    RELEASED_FILE,
    ReleaseBlockedError,
    approve_draft,
    build_draft,
    load_draft,
    render_report,
    save_draft,
)
from touchline.tools import ToolBox

METRICS_CALL = tool_use("get_player_metrics", {"player_id": FORWARD_A, "match_id": MATCH_ID})
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def report_json(number_in_text: str = "2") -> str:
    return json.dumps(
        {
            "headline": f"{number_in_text} shots and one goal.",
            "role_in_the_match": "Played as a forward.",
            "team_context": "The team won.",
            "findings": [
                {
                    "observation": "Received most balls centrally.",
                    "evidence": "4 open-play receptions.",
                    "metric_names": ["reception_zones"],
                }
            ],
            "where_to_receive": [
                {"zone": "final_third:centre", "rationale": "All 4 receptions came here."}
            ],
            "limitations": ["A single match."],
            "questions_for_staff": ["Was this the plan?"],
        }
    )


def analyst(con, responses: list[Any]) -> tuple[Any, ScriptedClient]:
    client = ScriptedClient(responses)
    return run_match_analyst(client, ToolBox(con), MATCH_ID, FORWARD_A, "Forward"), client


def test_analyst_produces_a_draft_pending_approval(con) -> None:
    result, client = analyst(
        con, [message([METRICS_CALL], "tool_use"), message([text(report_json())])]
    )
    assert result.status == "pending_approval"
    assert result.report.headline.startswith("2 shots")
    assert result.faithfulness.passed
    assert client.exhausted
    assert client.requests[0]["output_config"]["effort"] == "high"
    assert client.requests[0]["output_config"]["format"]["schema"] == REPORT_SCHEMA


def test_analyst_repairs_an_unsupported_number_once(con) -> None:
    result, client = analyst(
        con,
        [
            message([METRICS_CALL], "tool_use"),
            message([text(report_json("77"))]),
            message([text(report_json("2"))]),
        ],
    )
    assert result.status == "pending_approval"
    retry = client.requests[2]["messages"][-1]
    assert retry["role"] == "user"
    assert "77" in retry["content"]


def test_analyst_blocks_when_the_number_survives_repair(con) -> None:
    result, _ = analyst(
        con,
        [
            message([METRICS_CALL], "tool_use"),
            message([text(report_json("77"))]),
            message([text(report_json("78"))]),
        ],
    )
    assert result.status == "blocked"
    assert "78" in result.block_reason


def test_analyst_rejects_a_report_written_without_querying(con) -> None:
    # No numbers to catch, but a report with no queries behind it is still not acceptable.
    no_numbers = report_json("Several").replace("4 ", "Some ")
    result, client = analyst(con, [message([text(no_numbers)]), message([text(no_numbers)])])
    assert result.status == "blocked"
    assert "never queried" in result.block_reason
    assert "did not call any tools" in client.requests[1]["messages"][-1]["content"]


def test_analyst_blocks_on_refusal(con) -> None:
    result, _ = analyst(con, [refusal("cyber")])
    assert result.status == "blocked"
    assert "declined (cyber)" in result.block_reason


def test_analyst_blocks_on_truncation(con) -> None:
    result, _ = analyst(con, [message([text('{"headline": "trunc')], "max_tokens")])
    assert result.status == "blocked"
    assert "max_tokens" in result.block_reason


def test_analyst_blocks_on_invalid_json(con) -> None:
    result, _ = analyst(con, [message([METRICS_CALL], "tool_use"), message([text("not json")])])
    assert result.status == "blocked"
    assert "schema" in result.block_reason


def make_draft(con, tmp_path: Path, responses: list[Any] | None = None) -> Path:
    result, _ = analyst(
        con, responses or [message([METRICS_CALL], "tool_use"), message([text(report_json())])]
    )
    draft = build_draft(
        result,
        match_id=MATCH_ID,
        player_id=FORWARD_A,
        player="Forward",
        team=ALPHA,
        language="en",
        model="claude-opus-5-5",
        table="| Metric | Value |",
        charts=["charts/shots.png"],
    )
    return save_draft(draft, tmp_path)


def test_nothing_is_released_before_approval(con, tmp_path: Path) -> None:
    path = make_draft(con, tmp_path)
    assert load_draft(path).status == "pending_approval"
    assert not (tmp_path / RELEASED_FILE).exists()
    with pytest.raises(ReleaseBlockedError, match="approved"):
        render_report(load_draft(path))


def test_approval_needs_a_name(con, tmp_path: Path) -> None:
    with pytest.raises(ReleaseBlockedError, match="name"):
        approve_draft(make_draft(con, tmp_path), "   ", NOW)


def test_approval_releases_with_attribution(con, tmp_path: Path) -> None:
    released = approve_draft(make_draft(con, tmp_path), "A. Analyst", NOW)
    content = released.read_text(encoding="utf-8")
    assert "Data: StatsBomb Open Data" in content
    assert "![StatsBomb](statsbomb_logo.png)" in content
    assert (tmp_path / "statsbomb_logo.png").exists()
    assert "Not affiliated" in content
    assert "Approved by A. Analyst" in content
    assert load_draft(tmp_path / "draft.json").status == "approved"


def test_a_blocked_draft_cannot_be_approved(con, tmp_path: Path) -> None:
    path = make_draft(con, tmp_path, [refusal("cyber")])
    with pytest.raises(ReleaseBlockedError, match="blocked"):
        approve_draft(path, "A. Analyst", NOW)
    assert not (tmp_path / RELEASED_FILE).exists()


def test_a_draft_edited_after_the_fact_fails_at_approval(con, tmp_path: Path) -> None:
    path = make_draft(con, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["report"]["headline"] = "He had 99 shots."
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReleaseBlockedError, match="99"):
        approve_draft(path, "A. Analyst", NOW)


def coach(con, responses: list[Any]) -> tuple[CoachSession, ScriptedClient]:
    client = ScriptedClient(responses)
    return CoachSession(client, ToolBox(con), FORWARD_A, "Forward", MATCH_ID), client


@pytest.mark.parametrize(
    ("question", "route", "language", "word"),
    [
        ("My hamstring is sore, should I play?", Route.MEDICAL, "en", "doctor"),
        ("Bu rakamlarla sözleşmemi uzatmalı mıyım?", Route.CONTRACT, "tr", "Sözleşme"),
        ("¿Crees que un fichaje por otro club me vendría bien?", Route.TRANSFER, "es", "traspasos"),
    ],
)
def test_coach_refers_out_of_scope_questions_without_calling_the_model(
    con, question: str, route: Route, language: str, word: str
) -> None:
    session, client = coach(con, [])
    reply = session.ask(question)
    assert reply.outcome == "referred"
    assert reply.route is route
    assert reply.language == language
    assert word in reply.text
    assert client.requests == []


def test_coach_answers_from_tool_results(con) -> None:
    session, client = coach(
        con,
        [
            message([METRICS_CALL], "tool_use"),
            message([text("Topu 4 kez aldın, medyan alan 5,0 yard.")]),
        ],
    )
    reply = session.ask("Topu nerede aldım?")
    assert reply.outcome == "answered"
    assert reply.language == "tr"
    assert reply.faithfulness.passed
    assert reply.tool_calls_made == 1
    assert client.requests[0]["output_config"] == {"effort": "medium"}
    assert f"player_id {FORWARD_A}" in client.requests[0]["messages"][0]["content"]


def test_coach_context_stays_out_of_the_cached_system_prompt(con) -> None:
    session, client = coach(
        con, [message([METRICS_CALL], "tool_use"), message([text("No numbers here.")])]
    )
    session.ask("What is a progressive pass?")
    assert client.requests[0]["system"][0]["text"] == COACH_SYSTEM
    assert "Forward" not in COACH_SYSTEM


def test_coach_retries_then_withholds_an_unsupported_number(con) -> None:
    session, client = coach(
        con,
        [
            message([METRICS_CALL], "tool_use"),
            message([text("You took 77 shots.")]),
            message([text("You took 78 shots.")]),
        ],
    )
    reply = session.ask("How many shots did I take?")
    assert reply.outcome == "unverified"
    assert "77" not in reply.text
    assert "78" not in reply.text
    assert "[Automated check]" in client.requests[2]["messages"][-1]["content"]
    assert session._messages == [], "a withheld answer must not stay in the history"


def test_coach_repair_can_recover(con) -> None:
    session, _ = coach(
        con,
        [
            message([METRICS_CALL], "tool_use"),
            message([text("You took 77 shots.")]),
            message([text("You took 2 shots.")]),
        ],
    )
    reply = session.ask("How many shots did I take?")
    assert reply.outcome == "answered"
    assert reply.text == "You took 2 shots."


def test_coach_evidence_accumulates_across_turns(con) -> None:
    session, _ = coach(
        con,
        [
            message([METRICS_CALL], "tool_use"),
            message([text("You took 2 shots.")]),
            message([text("As I said, 2 shots.")]),
        ],
    )
    assert session.ask("How many shots?").outcome == "answered"
    assert session.ask("Remind me?").outcome == "answered"


def test_coach_is_told_to_query_when_it_skips_the_tools(con) -> None:
    session, client = coach(
        con,
        [
            message([text("Probably on the wing.")]),
            message([METRICS_CALL], "tool_use"),
            message([text("You took 2 shots.")]),
        ],
    )
    assert session.ask("How many shots?").outcome == "answered"
    assert "did not call any tools" in client.requests[1]["messages"][-1]["content"]


def test_coach_withholds_an_answer_with_no_tool_calls_behind_it(con) -> None:
    # Even with no number to catch, an answer no query backs is not given.
    session, _ = coach(con, [message([text("On the wing.")]), message([text("The wing.")])])
    reply = session.ask("Where do I play?")
    assert reply.outcome == "unverified"
    assert "wing" not in reply.text


def test_a_withheld_turn_leaves_no_evidence_behind(con) -> None:
    session, client = coach(
        con,
        [
            message([METRICS_CALL], "tool_use"),
            message([text("You took 77 shots.")]),
            message([text("You took 77 shots.")]),
            # The next turn has no tools and its "2" is in the withdrawn evidence.
            message([text("You took 2 shots.")]),
            message([text("You took 2 shots.")]),
        ],
    )
    assert session.ask("How many shots?").outcome == "unverified"
    assert session.ask("How many shots again?").outcome == "unverified"
    assert client.exhausted


def test_coach_handles_refusal(con) -> None:
    session, _ = coach(con, [refusal("cyber")])
    reply = session.ask("¿Dónde debo recibir el balón?")
    assert reply.outcome == "declined"
    assert reply.language == "es"


def test_coach_handles_truncation(con) -> None:
    session, _ = coach(con, [message([text("half")], "max_tokens")])
    assert session.ask("Where should I receive the ball?").outcome == "incomplete"


def test_prompts_state_the_boundaries_and_are_stable() -> None:
    for prompt in (ANALYST_SYSTEM, COACH_SYSTEM):
        assert "<numbers>" in prompt
        assert "medical" in prompt
        assert "contracts" in prompt
        assert "transfers" in prompt
        assert "space_at_reception" in prompt
