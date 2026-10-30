"""The match analyst: writes a structured report, which a human must approve before release."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from anthropic import transform_schema
from pydantic import BaseModel, ConfigDict, ValidationError

from touchline.agents.loop import DEFAULT_MODEL, AgentConfig, ClaudeClient, Usage, run_agent
from touchline.agents.prompts import ANALYST_SYSTEM
from touchline.checks.faithfulness import FaithfulnessResult, check_faithfulness
from touchline.tools import ToolBox, ToolCall

_EFFORT = "high"
_MAX_TOKENS = 16000
_LANGUAGES = {"en": "English", "es": "Spanish", "tr": "Turkish"}


class _ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Finding(_ReportModel):
    observation: str
    evidence: str
    metric_names: list[str]


class ReceivingZone(_ReportModel):
    zone: str
    rationale: str


class PlayerReport(_ReportModel):
    """Narrative only. Ids and tables are added by code, so the model cannot mistype them."""

    headline: str
    role_in_the_match: str
    team_context: str
    findings: list[Finding]
    where_to_receive: list[ReceivingZone]
    limitations: list[str]
    questions_for_staff: list[str]


REPORT_SCHEMA: dict[str, Any] = transform_schema(PlayerReport)

DraftStatus = Literal["pending_approval", "blocked"]


@dataclass
class AnalystResult:
    status: DraftStatus
    report: PlayerReport | None
    faithfulness: FaithfulnessResult | None
    block_reason: str | None
    tool_calls: list[ToolCall]
    usage: Usage = field(default_factory=Usage)


def report_prose(report: PlayerReport) -> str:
    """All model-written text in a report, for the faithfulness check."""
    parts = [report.headline, report.role_in_the_match, report.team_context]
    for finding in report.findings:
        parts += [finding.observation, finding.evidence]
    for zone in report.where_to_receive:
        parts += [zone.rationale]
    parts += report.limitations
    parts += report.questions_for_staff
    return "\n".join(parts)


def _request(match_id: int, player_id: int, player: str, language: str) -> str:
    return (
        f"Write the report on {player} (player_id {player_id}) in match_id {match_id}. "
        f"Write it in {_LANGUAGES[language]}."
    )


def run_match_analyst(
    client: ClaudeClient,
    toolbox: ToolBox,
    match_id: int,
    player_id: int,
    player: str,
    language: str = "en",
    model: str = DEFAULT_MODEL,
) -> AnalystResult:
    """Produce a draft. A draft that fails the checks once gets one repair attempt."""
    config = AgentConfig(
        system=ANALYST_SYSTEM,
        effort=_EFFORT,
        max_tokens=_MAX_TOKENS,
        model=model,
        output_schema=REPORT_SCHEMA,
    )
    messages: list[Any] = [
        {"role": "user", "content": _request(match_id, player_id, player, language)}
    ]
    usage = Usage()
    report: PlayerReport | None = None
    verdict: FaithfulnessResult | None = None

    for attempt in range(2):
        run = run_agent(client, config, toolbox, messages)
        usage.add(run.usage)
        if run.status == "refused":
            return _blocked(f"the model declined ({run.refusal_category})", toolbox, usage)
        if run.status == "incomplete":
            return _blocked(f"the run stopped early ({run.stop_reason})", toolbox, usage)

        if not toolbox.evidence:
            problem = "You did not call any tools. Query the data, then write the report."
        else:
            try:
                report = PlayerReport.model_validate(json.loads(run.text))
            except (json.JSONDecodeError, ValidationError) as error:
                return _blocked(f"the report did not match the schema: {error}", toolbox, usage)
            verdict = check_faithfulness(report_prose(report), toolbox.evidence, language)
            if verdict.passed:
                return AnalystResult(
                    "pending_approval", report, verdict, None, list(toolbox.ledger), usage
                )
            problem = (
                "These numbers do not appear in your tool results: "
                f"{', '.join(verdict.unsupported)}. Query the data to back them, or remove them. "
                "Do not derive numbers yourself. Return the whole report again."
            )
        if attempt == 0:
            messages.append({"role": "user", "content": problem})

    if verdict is None:
        return _blocked("the model never queried the data", toolbox, usage)
    reason = f"unsupported numbers after repair: {', '.join(verdict.unsupported)}"
    return AnalystResult("blocked", report, verdict, reason, list(toolbox.ledger), usage)


def _blocked(reason: str, toolbox: ToolBox, usage: Usage) -> AnalystResult:
    return AnalystResult("blocked", None, None, reason, list(toolbox.ledger), usage)
