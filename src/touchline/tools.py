"""The agents' read-only tools.

The model gets a closed set of typed functions, never SQL. Each tool validates its input
against the same Pydantic model that generates its strict JSON schema, queries DuckDB
through a read-only connection with bound parameters, and returns derived aggregates as
JSON. Raw events and freeze frames never leave the machine through these tools.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import duckdb
from anthropic import transform_schema
from pydantic import BaseModel, ConfigDict, ValidationError

from touchline.metrics import (
    METRICS,
    match_summary,
    player_metrics,
    player_shots,
    reception_zones,
    role_comparison,
)
from touchline.metrics.network import player_pass_links
from touchline.metrics.player import find_players
from touchline.metrics.team import UnknownEntityError, list_matches


class _Input(BaseModel):
    """Base for tool inputs. Strict tools need every field required and no extras."""

    model_config = ConfigDict(extra="forbid")


class ListMatchesInput(_Input):
    pass


class FindPlayerInput(_Input):
    name: str


class MatchInput(_Input):
    match_id: int


class PlayerScopeInput(_Input):
    player_id: int
    match_id: int | None


class PlayerInMatchInput(_Input):
    player_id: int
    match_id: int


class MetricDefinitionInput(_Input):
    metric: str


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    input_model: type[_Input]
    run: Callable[[duckdb.DuckDBPyConnection, Any], Any]


_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        "compare_to_role",
        "Compare a player with peers in the same role group: per-90 values, median space at "
        "reception and between-the-lines rate, with each metric's peer median and rank. Use it "
        "for questions about a player's role. match_id null means all ingested matches; peers "
        "always use all ingested matches.",
        PlayerScopeInput,
        lambda con, args: role_comparison(con, args.player_id, args.match_id),
    ),
    _Tool(
        "find_player",
        "Find players by a fragment of their name, ignoring case and accents. Returns player_id, "
        "name as it appears in the data, team and total minutes. Call this first when you have "
        "only a name.",
        FindPlayerInput,
        lambda con, args: {"matches": find_players(con, args.name)},
    ),
    _Tool(
        "get_match_summary",
        "Score and team totals for one match: shots, xG, passes, progressive actions, pressures.",
        MatchInput,
        lambda con, args: match_summary(con, args.match_id),
    ),
    _Tool(
        "get_metric_definition",
        "Definition and limitations of one metric. Valid names: "
        + ", ".join(sorted(METRICS))
        + ".",
        MetricDefinitionInput,
        lambda con, args: _metric_definition(args.metric),
    ),
    _Tool(
        "get_pass_links",
        "Who a player exchanged completed open-play passes with in one match, in each direction.",
        PlayerInMatchInput,
        lambda con, args: player_pass_links(con, args.match_id, args.player_id),
    ),
    _Tool(
        "get_player_metrics",
        "All metrics for one player: minutes, positions played, shots, xG, passes, progressive "
        "passes and carries, pressures by third, per-90 values and reception quality from 360 "
        "frames. match_id null means all ingested matches.",
        PlayerScopeInput,
        lambda con, args: player_metrics(con, args.player_id, args.match_id),
    ),
    _Tool(
        "get_player_shots",
        "Each of a player's shots: match, minute, outcome, type, body part and xG. match_id null "
        "means all ingested matches.",
        PlayerScopeInput,
        lambda con, args: player_shots(con, args.player_id, args.match_id),
    ),
    _Tool(
        "get_reception_zones",
        "Where a player received the ball, by zone (third:channel): count, share, median space "
        "and between-the-lines count, next to how role peers' receptions were distributed. Use "
        "this for questions about where to receive the ball. match_id null means all matches.",
        PlayerScopeInput,
        lambda con, args: reception_zones(con, args.player_id, args.match_id),
    ),
    _Tool(
        "list_matches",
        "List every ingested match with its id, date, stage, teams and score.",
        ListMatchesInput,
        lambda con, args: {"matches": list_matches(con)},
    ),
)


def _metric_definition(metric: str) -> dict[str, str]:
    definition = METRICS.get(metric)
    if definition is None:
        raise UnknownEntityError(f"unknown metric {metric!r}; valid: {', '.join(sorted(METRICS))}")
    return {
        "metric": definition.name,
        "definition": definition.definition,
        "limitations": definition.limitations,
    }


def _input_schema(model: type[_Input]) -> dict[str, Any]:
    schema = transform_schema(model)
    # A tool with no parameters comes back without `required`; strict tools state it anyway.
    schema.setdefault("required", [])
    return schema


def tool_definitions() -> list[dict[str, Any]]:
    """Tool definitions for the Messages API, in a fixed order so the prompt cache holds."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "strict": True,
            "input_schema": _input_schema(tool.input_model),
        }
        for tool in _TOOLS
    ]


@dataclass(frozen=True)
class ToolCall:
    """One executed tool call. The ledger of these is the evidence for faithfulness checks."""

    name: str
    arguments: dict[str, Any]
    output: Any
    is_error: bool

    @property
    def content(self) -> str:
        return json.dumps(self.output, ensure_ascii=False, sort_keys=True, default=str)


@dataclass
class ToolBox:
    """Runs tools against one read-only connection and records every call."""

    con: duckdb.DuckDBPyConnection
    ledger: list[ToolCall] = field(default_factory=list)

    def run(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        call = self._execute(name, arguments)
        self.ledger.append(call)
        return call

    def _execute(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        tool = next((item for item in _TOOLS if item.name == name), None)
        if tool is None:
            return ToolCall(name, arguments, {"error": f"unknown tool {name!r}"}, True)
        try:
            parsed = tool.input_model.model_validate(arguments)
        except ValidationError as error:
            return ToolCall(name, arguments, {"error": f"invalid input: {error}"}, True)
        try:
            return ToolCall(name, arguments, tool.run(self.con, parsed), False)
        except UnknownEntityError as error:
            return ToolCall(name, arguments, {"error": str(error)}, True)

    @property
    def evidence(self) -> list[Any]:
        """Outputs of successful calls. Errors and arguments do not count as evidence."""
        return [call.output for call in self.ledger if not call.is_error]
