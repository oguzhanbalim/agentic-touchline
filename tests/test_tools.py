"""Tool schemas and the read-only guarantee."""

from __future__ import annotations

import json

import pytest

from synthetic import FORWARD_A, MATCH_ID
from touchline.metrics import METRICS
from touchline.tools import ToolBox, tool_definitions


def _objects(schema: dict) -> list[dict]:
    found = []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            found.append(schema)
        for value in schema.values():
            if isinstance(value, dict):
                found += _objects(value)
            elif isinstance(value, list):
                for item in value:
                    found += _objects(item)
    return found


def test_tools_are_strict_and_every_object_is_closed() -> None:
    for tool in tool_definitions():
        assert tool["strict"] is True
        for schema in _objects(tool["input_schema"]):
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])


def test_tool_order_is_stable_for_the_prompt_cache() -> None:
    names = [tool["name"] for tool in tool_definitions()]
    assert names == sorted(names)
    assert tool_definitions() == tool_definitions()


def test_no_tool_accepts_sql() -> None:
    for tool in tool_definitions():
        assert not {"sql", "query"} & set(tool["input_schema"]["properties"])


def test_get_player_metrics_runs_and_is_recorded(con) -> None:
    toolbox = ToolBox(con)
    call = toolbox.run("get_player_metrics", {"player_id": FORWARD_A, "match_id": MATCH_ID})
    assert not call.is_error
    assert json.loads(call.content)["goals"] == 1
    assert toolbox.evidence == [call.output]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("drop_everything", {}),
        ("get_player_metrics", {"player_id": "1; DROP TABLE events", "match_id": None}),
        ("get_player_metrics", {"player_id": 1, "match_id": None, "sql": "SELECT 1"}),
        ("get_player_metrics", {"player_id": FORWARD_A}),
        ("get_player_metrics", {"player_id": 123456, "match_id": None}),
        ("get_match_summary", {"match_id": 999}),
        ("get_metric_definition", {"metric": "vibes"}),
    ],
)
def test_bad_calls_return_errors_that_are_not_evidence(con, name: str, arguments: dict) -> None:
    toolbox = ToolBox(con)
    call = toolbox.run(name, arguments)
    assert call.is_error
    assert "error" in call.output
    assert toolbox.evidence == []
    assert len(toolbox.ledger) == 1


def test_a_hostile_name_is_only_ever_a_bound_value(con) -> None:
    toolbox = ToolBox(con)
    call = toolbox.run("find_player", {"name": "'; DROP TABLE events; --"})
    assert call.output == {"matches": []}
    assert con.execute("SELECT count(*) FROM events").fetchone()[0] > 0


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("list_matches", {}),
        ("find_player", {"name": "alpha"}),
        ("get_match_summary", {"match_id": MATCH_ID}),
        ("get_player_shots", {"player_id": FORWARD_A, "match_id": None}),
        ("get_pass_links", {"player_id": FORWARD_A, "match_id": MATCH_ID}),
        ("get_reception_zones", {"player_id": FORWARD_A, "match_id": None}),
        ("compare_to_role", {"player_id": FORWARD_A, "match_id": None}),
        ("get_metric_definition", {"metric": "space_at_reception"}),
    ],
)
def test_every_tool_returns_json(con, name: str, arguments: dict) -> None:
    call = ToolBox(con).run(name, arguments)
    assert not call.is_error
    json.loads(call.content)


def test_metric_definition_comes_from_the_registry(con) -> None:
    call = ToolBox(con).run("get_metric_definition", {"metric": "pass_network"})
    assert call.output["definition"] == METRICS["pass_network"].definition


def test_tools_expose_no_raw_coordinates_or_event_ids(con) -> None:
    """The data licence forbids redistribution, so tools return aggregates only."""
    call = ToolBox(con).run("get_player_shots", {"player_id": FORWARD_A, "match_id": None})
    for shot in call.output["shots"]:
        assert not {"x", "y", "event_id", "location"} & set(shot)
