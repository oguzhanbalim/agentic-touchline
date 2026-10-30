"""The agentic loop against a scripted client, including every stop reason."""

from __future__ import annotations

import json
from typing import Any

import pytest
from anthropic import Anthropic, DefaultHttpxClient
from httpx2 import MockTransport, Request, Response

from fakes import ScriptedClient, message, refusal, text, thinking, tool_use
from synthetic import FORWARD_A, MATCH_ID
from touchline.agents.loop import AgentConfig, build_request, run_agent
from touchline.tools import ToolBox

CONFIG = AgentConfig(system="You are a test.", effort="medium", max_tokens=1000)


def start() -> list[Any]:
    return [{"role": "user", "content": "hello"}]


def test_request_follows_the_model_rules() -> None:
    request = build_request(CONFIG, start())
    assert request["model"] == "claude-opus-5-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "medium"}
    assert request["tool_choice"] == {"type": "auto"}
    assert all(tool["strict"] for tool in request["tools"])
    for banned in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert banned not in request
    assert "budget_tokens" not in request["thinking"]


def test_request_caches_the_stable_prefix_and_the_conversation_tail() -> None:
    request = build_request(CONFIG, start())
    assert request["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert request["cache_control"] == {"type": "ephemeral"}


def test_request_prefix_is_byte_identical_across_conversations() -> None:
    def prefix(messages: list[Any]) -> str:
        request = build_request(CONFIG, messages)
        return json.dumps([request["tools"], request["system"]], sort_keys=False)

    assert prefix(start()) == prefix([{"role": "user", "content": "something else"}])


def test_structured_output_sits_beside_effort() -> None:
    schema = {"type": "object", "properties": {}, "additionalProperties": False, "required": []}
    config = AgentConfig(system="x", effort="high", max_tokens=1, output_schema=schema)
    assert build_request(config, start())["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": schema},
    }


def test_no_prefill_in_any_request(con) -> None:
    client = ScriptedClient(
        [
            message([tool_use("list_matches", {})], "tool_use"),
            message([text("done")]),
        ]
    )
    run_agent(client, CONFIG, ToolBox(con), start())
    for request in client.requests:
        assert request["messages"][-1]["role"] == "user"


def test_tool_use_then_answer(con) -> None:
    arguments = {"player_id": FORWARD_A, "match_id": MATCH_ID}
    client = ScriptedClient(
        [
            message([thinking(), tool_use("get_player_metrics", arguments)], "tool_use"),
            message([thinking(), text("One goal.")]),
        ]
    )
    toolbox = ToolBox(con)
    messages = start()
    run = run_agent(client, CONFIG, toolbox, messages)
    assert run.status == "completed"
    assert run.text == "One goal."
    assert run.tool_calls_made == 1
    assert len(toolbox.ledger) == 1

    second = client.requests[1]["messages"]
    assert second[1]["role"] == "assistant"
    assert second[1]["content"][0].type == "thinking", "thinking blocks go back unchanged"
    result = second[2]["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "toolu_1"
    assert json.loads(result["content"])["goals"] == 1


def test_tool_error_is_flagged_and_the_loop_continues(con) -> None:
    client = ScriptedClient(
        [
            message([tool_use("get_match_summary", {"match_id": 999})], "tool_use"),
            message([text("That match is not in the data.")]),
        ]
    )
    run = run_agent(client, CONFIG, ToolBox(con), start())
    assert run.status == "completed"
    assert client.requests[1]["messages"][2]["content"][0]["is_error"] is True


def test_parallel_tool_calls_all_run(con) -> None:
    client = ScriptedClient(
        [
            message(
                [
                    tool_use("list_matches", {}, "toolu_a"),
                    tool_use("find_player", {"name": "alpha"}, "toolu_b"),
                ],
                "tool_use",
            ),
            message([text("done")]),
        ]
    )
    run = run_agent(client, CONFIG, ToolBox(con), start())
    assert run.tool_calls_made == 2
    ids = [item["tool_use_id"] for item in client.requests[1]["messages"][2]["content"]]
    assert ids == ["toolu_a", "toolu_b"]


def test_refusal_is_reported_with_its_category_and_not_replayed(con) -> None:
    client = ScriptedClient([refusal("bio")])
    messages = start()
    run = run_agent(client, CONFIG, ToolBox(con), messages)
    assert run.status == "refused"
    assert run.refusal_category == "bio"
    assert run.text == ""
    assert messages == start()


@pytest.mark.parametrize("reason", ["max_tokens", "model_context_window_exceeded", "brand_new"])
def test_early_stops_are_incomplete_not_answers(con, reason: str) -> None:
    client = ScriptedClient([message([text("half an ans")], reason)])
    run = run_agent(client, CONFIG, ToolBox(con), start())
    assert run.status == "incomplete"
    assert run.stop_reason == reason


def test_tool_use_stop_without_a_block_is_incomplete(con) -> None:
    run = run_agent(ScriptedClient([message([], "tool_use")]), CONFIG, ToolBox(con), start())
    assert run.status == "incomplete"


def test_pause_turn_resumes_without_an_extra_user_message(con) -> None:
    client = ScriptedClient([message([text("working")], "pause_turn"), message([text("done")])])
    run = run_agent(client, CONFIG, ToolBox(con), start())
    assert run.status == "completed"
    assert client.requests[1]["messages"][-1]["role"] == "assistant"


def test_iteration_cap_stops_a_runaway_loop(con) -> None:
    config = AgentConfig(system="x", effort="low", max_tokens=1, max_iterations=3)
    responses = [message([tool_use("list_matches", {})], "tool_use") for _ in range(3)]
    run = run_agent(ScriptedClient(responses), config, ToolBox(con), start())
    assert run.status == "incomplete"
    assert run.iterations == 3


def test_usage_is_summed_across_iterations(con) -> None:
    client = ScriptedClient(
        [message([tool_use("list_matches", {})], "tool_use"), message([text("done")])]
    )
    run = run_agent(client, CONFIG, ToolBox(con), start())
    assert run.usage.input_tokens == 20
    assert run.usage.cache_read_input_tokens == 6


def test_the_real_sdk_serialises_our_request(con) -> None:
    """Drive the actual SDK against a fake HTTP transport, with no key and no network."""
    bodies: list[dict[str, Any]] = []
    replies = [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "list_matches", "input": {}},
            ],
        },
        {
            "id": "msg_2",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": "done"}],
        },
    ]

    def handler(request: Request) -> Response:
        bodies.append(json.loads(request.content))
        assert request.url.path == "/v1/messages"
        return Response(200, json=replies[len(bodies) - 1])

    client = Anthropic(
        api_key="test-key", http_client=DefaultHttpxClient(transport=MockTransport(handler))
    )
    run = run_agent(client, CONFIG, ToolBox(con), start())
    assert run.status == "completed"
    assert run.text == "done"
    first = bodies[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "medium"}
    assert first["tools"][0]["strict"] is True
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    # The assistant's SDK objects must serialise back into the second request.
    assert bodies[1]["messages"][1]["content"][0]["type"] == "tool_use"
    assert bodies[1]["messages"][2]["content"][0]["type"] == "tool_result"
