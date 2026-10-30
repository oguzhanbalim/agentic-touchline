"""Report drafts and the human approval gate.

A draft is written to disk with the tool results behind it. Only `approve_draft` writes the
releasable report, and it re-runs the faithfulness check from the stored tool results first,
so a draft edited after the fact cannot pass with a new number.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

from touchline.agents.analyst import AnalystResult, PlayerReport, report_prose
from touchline.charts import pass_network_chart, progressive_map, reception_map, shot_map
from touchline.checks.faithfulness import check_faithfulness
from touchline.config import ATTRIBUTION, DISCLAIMER
from touchline.metrics import match_summary, player_metrics
from touchline.metrics.player import fold_name

DRAFT_FILE = "draft.json"
RELEASED_FILE = "report.md"
_LOGO_FILE = "statsbomb_logo.png"


class ReleaseBlockedError(RuntimeError):
    """The draft cannot be released."""


class Draft(BaseModel):
    match_id: int
    player_id: int
    player: str
    team: str
    language: str
    model: str
    status: Literal["pending_approval", "blocked", "approved"]
    block_reason: str | None = None
    report: PlayerReport | None = None
    metrics_table: str
    charts: list[str]
    tool_outputs: list[Any]
    approved_by: str | None = None
    approved_at: str | None = None


def report_dir(root: Path, match_id: int, player: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", fold_name(player)).strip("-")
    return root / f"{match_id}-{slug or 'player'}"


def render_charts(
    con: duckdb.DuckDBPyConnection, match_id: int, player_id: int, team: str, directory: Path
) -> list[str]:
    """Render the report's charts. Returns file names relative to the report directory."""
    charts = directory / "charts"
    pass_network_chart(con, match_id, team, charts / "pass_network.png")
    shot_map(con, match_id, team, charts / "shots.png")
    reception_map(con, player_id, match_id, charts / "receptions.png")
    progressive_map(con, player_id, match_id, charts / "progressive.png")
    return [
        "charts/pass_network.png",
        "charts/shots.png",
        "charts/receptions.png",
        "charts/progressive.png",
    ]


def metrics_table(con: duckdb.DuckDBPyConnection, match_id: int, player_id: int) -> str:
    """A Markdown table computed entirely in code, shown beside the model's narrative."""
    metrics = player_metrics(con, player_id, match_id)
    summary = match_summary(con, match_id)
    receptions = metrics["receptions"]
    rows = [
        ("Match", f"{summary['home_team']} {summary['home_score']}-{summary['away_score']} "
                  f"{summary['away_team']}"),
        ("Minutes", metrics["minutes"]),
        ("Positions", ", ".join(item["position"] for item in metrics["positions"]) or "none"),
        ("Shots / goals / xG", f"{metrics['shots']} / {metrics['goals']} / {metrics['xg']}"),
        ("Passes completed / attempted",
         f"{metrics['passes_completed']} / {metrics['passes_attempted']}"),
        ("Progressive passes", metrics["progressive_passes"]),
        ("Progressive carries", metrics["progressive_carries"]),
        ("Pressures", metrics["pressures"]),
        ("Open-play receptions", receptions["open_play_receptions"]),
        ("Median space at reception, yards",
         f"{receptions['median_space_yards']} ({receptions['space_measured']} measured, "
         f"{receptions['space_censored']} censored)"),
        ("Receptions between the lines",
         f"{receptions['between_lines']} of {receptions['between_lines_assessable']} assessable"),
    ]  # fmt: skip
    lines = ["| Metric | Value |", "| --- | --- |"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    return "\n".join(lines)


def build_draft(
    result: AnalystResult,
    *,
    match_id: int,
    player_id: int,
    player: str,
    team: str,
    language: str,
    model: str,
    table: str,
    charts: list[str],
) -> Draft:
    return Draft(
        match_id=match_id,
        player_id=player_id,
        player=player,
        team=team,
        language=language,
        model=model,
        status=result.status,
        block_reason=result.block_reason,
        report=result.report,
        metrics_table=table,
        charts=charts,
        tool_outputs=[call.output for call in result.tool_calls if not call.is_error],
    )


def save_draft(draft: Draft, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / DRAFT_FILE
    path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_draft(path: Path) -> Draft:
    return Draft.model_validate(json.loads(path.read_text(encoding="utf-8")))


def approve_draft(draft_path: Path, approved_by: str, now: datetime) -> Path:
    """Record a person's approval and write the releasable report."""
    if not approved_by.strip():
        raise ReleaseBlockedError("approval needs the name of the person approving")
    draft = load_draft(draft_path)
    if draft.status == "blocked" or draft.report is None:
        raise ReleaseBlockedError(f"draft is blocked: {draft.block_reason}")
    verdict = check_faithfulness(report_prose(draft.report), draft.tool_outputs, draft.language)
    if not verdict.passed:
        raise ReleaseBlockedError(f"unsupported numbers: {', '.join(verdict.unsupported)}")

    draft = draft.model_copy(
        update={
            "status": "approved",
            "approved_by": approved_by.strip(),
            "approved_at": now.isoformat(timespec="seconds"),
        }
    )
    directory = draft_path.parent
    save_draft(draft, directory)
    shutil.copyfile(
        str(resources.files("touchline") / "assets" / _LOGO_FILE), directory / _LOGO_FILE
    )
    released = directory / RELEASED_FILE
    released.write_text(render_report(draft), encoding="utf-8")
    return released


def render_report(draft: Draft) -> str:
    """Render an approved draft as Markdown, with the required attribution."""
    if draft.report is None or draft.status != "approved":
        raise ReleaseBlockedError("only an approved draft can be rendered for release")
    report = draft.report
    lines = [
        f"# {draft.player}: match {draft.match_id}",
        "",
        f"**{report.headline}**",
        "",
        draft.metrics_table,
        "",
        "## Role in the match",
        "",
        report.role_in_the_match,
        "",
        "## Team context",
        "",
        report.team_context,
        "",
        "## Findings",
        "",
    ]
    for finding in report.findings:
        lines += [f"- {finding.observation} {finding.evidence}"]
    lines += ["", "## Where to receive the ball (hypotheses for the staff)", ""]
    for zone in report.where_to_receive:
        lines += [f"- `{zone.zone}`: {zone.rationale}"]
    lines += ["", "## Limitations", ""]
    lines += [f"- {item}" for item in report.limitations]
    lines += ["", "## Questions for staff", ""]
    lines += [f"- {item}" for item in report.questions_for_staff]
    lines += ["", "## Charts", ""]
    lines += [f"![{chart}]({chart})" for chart in draft.charts]
    lines += [
        "",
        "---",
        "",
        f"![StatsBomb]({_LOGO_FILE})",
        "",
        f"{ATTRIBUTION}. Analysis is not necessarily the opinion of StatsBomb. {DISCLAIMER}",
        "",
        f"Written by {draft.model}. Approved by {draft.approved_by} at {draft.approved_at}.",
        "",
    ]
    return "\n".join(lines)
