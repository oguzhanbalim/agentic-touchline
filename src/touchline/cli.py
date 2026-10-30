"""Command line: `touchline ingest`, `report`, `approve`, `chat` and `check-boundary`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import duckdb

from touchline.agents.analyst import run_match_analyst
from touchline.agents.coach import CoachSession
from touchline.agents.loop import DEFAULT_MODEL, ClaudeClient
from touchline.checks.boundary_cases import BOUNDARY_CASES
from touchline.checks.routing import detect_language, route_question
from touchline.config import (
    ATTRIBUTION,
    DEFAULT_COMPETITION_ID,
    DEFAULT_SEASON_ID,
    DEFAULT_TEAM,
    DISCLAIMER,
    default_paths,
)
from touchline.db import connect
from touchline.fetch import OpenDataCache
from touchline.ingest import IntegrityError, ingest
from touchline.metrics import reception_zones, role_comparison
from touchline.metrics.player import find_players, fold_name
from touchline.metrics.team import UnknownEntityError, match_row
from touchline.report import (
    ReleaseBlockedError,
    approve_draft,
    build_draft,
    metrics_table,
    render_charts,
    report_dir,
    save_draft,
)
from touchline.tools import ToolBox

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_USAGE = 2

_NO_CREDENTIALS = (
    "No Claude API credentials found. Set ANTHROPIC_API_KEY. Metrics and charts work without "
    "one: try `touchline report --match <id> --player <name> --metrics-only`."
)


def _resolve_player(con: duckdb.DuckDBPyConnection, name: str) -> dict[str, Any]:
    matches = find_players(con, name)
    if not matches:
        raise SystemExit(f"No player matching {name!r}. Names are as they appear in the data.")
    exact = [row for row in matches if fold_name(row["player"]) == fold_name(name)]
    if len(exact) == 1:
        return exact[0]
    if len(matches) > 1:
        listing = "\n".join(f"  {row['player']} ({row['team']})" for row in matches)
        raise SystemExit(f"{name!r} matches more than one player:\n{listing}")
    return matches[0]


def _build_client() -> ClaudeClient:
    return anthropic.Anthropic()


def _is_missing_credentials(error: TypeError) -> bool:
    return "authentication" in str(error).lower()


def _cmd_ingest(args: argparse.Namespace) -> int:
    paths = default_paths()
    team = None if args.all_matches else args.team
    cache = OpenDataCache(paths.cache_dir)
    try:
        summary = ingest(cache, paths.db_path, args.competition, args.season, team)
    except IntegrityError as error:
        print(f"Integrity check failed: {error}", file=sys.stderr)
        return _EXIT_ERROR
    print(
        f"Ingested {summary.matches} matches, {summary.events} events, "
        f"{summary.frames} 360 frames, {summary.receptions} completed receptions "
        f"into {paths.db_path}.\n{ATTRIBUTION}. {DISCLAIMER}"
    )
    con = connect(paths.db_path)
    try:
        for row in con.execute(
            "SELECT match_id, match_date, home_team, home_score, away_score, away_team "
            "FROM matches ORDER BY match_date"
        ).fetchall():
            print(f"  {row[0]}  {row[1]}  {row[2]} {row[3]}-{row[4]} {row[5]}")
    finally:
        con.close()
    return _EXIT_OK


def _print_metrics(con: duckdb.DuckDBPyConnection, player_id: int, match_id: int) -> None:
    print(metrics_table(con, match_id, player_id))
    comparison = role_comparison(con, player_id, match_id)
    print(f"\nRole group: {comparison['role']}. Peers in the ingested data: {comparison['peers']}.")
    print("| Metric | Player | Peer median | Rank | Peers measured |")
    print("| --- | --- | --- | --- | --- |")
    for item in comparison["metrics"]:
        print(
            f"| {item['metric']} | {item['player_value']} | {item['peer_median']} "
            f"| {item['rank_including_player']} | {item['peers_measured']} |"
        )
    zones = reception_zones(con, player_id, match_id)
    print(f"\nTop reception zones of {zones['open_play_receptions']} open-play receptions:")
    print("| Zone | Receptions | Share % | Median space | Peer share % |")
    print("| --- | --- | --- | --- | --- |")
    for zone in zones["zones"][:5]:
        print(
            f"| {zone['zone']} | {zone['receptions']} | {zone['share_pct']} "
            f"| {zone['median_space_yards']} | {zone['peer_share_pct']} |"
        )


def _cmd_report(args: argparse.Namespace) -> int:
    paths = default_paths()
    con = connect(paths.db_path)
    try:
        match_row(con, args.match)
        player = _resolve_player(con, args.player)
        player_id, name = player["player_id"], player["player"]
        team_row = con.execute(
            "SELECT team FROM lineups WHERE match_id = ? AND player_id = ?",
            [args.match, player_id],
        ).fetchone()
        if team_row is None:
            print(f"{name} is not in the squad for match {args.match}.", file=sys.stderr)
            return _EXIT_USAGE
        team = team_row[0]

        directory = report_dir(Path(args.out), args.match, name)
        charts = render_charts(con, args.match, player_id, team, directory)
        table = metrics_table(con, args.match, player_id)
        if args.metrics_only:
            _print_metrics(con, player_id, args.match)
            print(f"\nCharts in {directory / 'charts'}.\n{ATTRIBUTION}. {DISCLAIMER}")
            return _EXIT_OK

        try:
            result = run_match_analyst(
                _build_client(),
                ToolBox(con),
                args.match,
                player_id,
                name,
                args.language,
                args.model,
            )
        except TypeError as error:
            if _is_missing_credentials(error):
                print(_NO_CREDENTIALS, file=sys.stderr)
                return _EXIT_USAGE
            raise
        except anthropic.APIError as error:
            print(f"Claude API error: {error}", file=sys.stderr)
            return _EXIT_ERROR
        draft = build_draft(
            result,
            match_id=args.match,
            player_id=player_id,
            player=name,
            team=team,
            language=args.language,
            model=args.model,
            table=table,
            charts=charts,
        )
        path = save_draft(draft, directory)
    finally:
        con.close()

    usage = result.usage
    print(
        f"Tokens: {usage.input_tokens} input, {usage.output_tokens} output, "
        f"{usage.cache_read_input_tokens} read from cache, "
        f"{usage.cache_creation_input_tokens} written to cache.",
        file=sys.stderr,
    )

    if draft.status == "blocked":
        print(f"Draft blocked: {draft.block_reason}\nSaved to {path}. It cannot be released.")
        return _EXIT_ERROR
    assert draft.report is not None
    print(draft.report.model_dump_json(indent=2))
    print(f"\nDraft saved to {path}. All numbers trace to tool results in this run.")
    print(f"It is not released. To release it: touchline approve {path} --by <your name>")
    return _EXIT_OK


def _cmd_approve(args: argparse.Namespace) -> int:
    try:
        released = approve_draft(Path(args.draft), args.by, datetime.now().astimezone())
    except ReleaseBlockedError as error:
        print(f"Not released: {error}", file=sys.stderr)
        return _EXIT_ERROR
    print(f"Released to {released}.")
    return _EXIT_OK


def _cmd_chat(args: argparse.Namespace) -> int:
    paths = default_paths()
    con = connect(paths.db_path)
    try:
        if args.match is not None:
            match_row(con, args.match)
        player = _resolve_player(con, args.player)
        session = CoachSession(
            _build_client(),
            ToolBox(con),
            player["player_id"],
            player["player"],
            args.match,
            args.model,
        )
        questions = [args.ask] if args.ask else None
        print(f"Coach for {player['player']}. {ATTRIBUTION}. Ctrl-D to leave.", file=sys.stderr)
        while True:
            if questions is not None:
                if not questions:
                    return _EXIT_OK
                question = questions.pop()
            else:
                try:
                    question = input("> ").strip()
                except EOFError:
                    return _EXIT_OK
                if not question:
                    continue
            try:
                reply = session.ask(question)
            except TypeError as error:
                if _is_missing_credentials(error):
                    print(_NO_CREDENTIALS, file=sys.stderr)
                    return _EXIT_USAGE
                raise
            except anthropic.APIError as error:
                print(f"Claude API error: {error}", file=sys.stderr)
                return _EXIT_ERROR
            print(reply.text)
    finally:
        con.close()


def _cmd_check_boundary(args: argparse.Namespace) -> int:
    """Run the 30 boundary questions through the offline routing rules."""
    failures = 0
    for case in BOUNDARY_CASES:
        route = route_question(case.question)
        language = detect_language(case.question)
        if route != case.route or language != case.language:
            failures += 1
            print(
                f"FAIL {case.question!r}: route {route} (want {case.route}), "
                f"language {language} (want {case.language})"
            )
    counts: dict[str, int] = {}
    for case in BOUNDARY_CASES:
        counts[case.route.value] = counts.get(case.route.value, 0) + 1
    print(json.dumps({"total": len(BOUNDARY_CASES), "failed": failures, "by_route": counts}))
    return _EXIT_ERROR if failures else _EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="touchline", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = commands.add_parser("ingest", help="download StatsBomb Open Data into DuckDB")
    ingest_cmd.add_argument("--competition", type=int, default=DEFAULT_COMPETITION_ID)
    ingest_cmd.add_argument("--season", type=int, default=DEFAULT_SEASON_ID)
    ingest_cmd.add_argument("--team", default=DEFAULT_TEAM, help="team name as the data spells it")
    ingest_cmd.add_argument("--all-matches", action="store_true", help="ingest the whole season")
    ingest_cmd.set_defaults(handler=_cmd_ingest)

    report = commands.add_parser("report", help="write a player report for one match")
    report.add_argument("--match", type=int, required=True)
    report.add_argument("--player", required=True)
    report.add_argument("--language", choices=("en", "es", "tr"), default="en")
    report.add_argument("--out", default="reports")
    report.add_argument("--model", default=DEFAULT_MODEL)
    report.add_argument(
        "--metrics-only", action="store_true", help="metrics and charts only, no model and no key"
    )
    report.set_defaults(handler=_cmd_report)

    approve = commands.add_parser("approve", help="approve a draft report and release it")
    approve.add_argument("draft")
    approve.add_argument("--by", required=True, help="name of the person approving")
    approve.set_defaults(handler=_cmd_approve)

    chat = commands.add_parser("chat", help="ask the coach about a player's role")
    chat.add_argument("--player", required=True)
    chat.add_argument("--match", type=int, default=None)
    chat.add_argument("--ask", default=None, help="ask one question and exit")
    chat.add_argument("--model", default=DEFAULT_MODEL)
    chat.set_defaults(handler=_cmd_chat)

    boundary = commands.add_parser(
        "check-boundary", help="run the boundary question set through offline routing"
    )
    boundary.set_defaults(handler=_cmd_check_boundary)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileNotFoundError, UnknownEntityError) as error:
        print(str(error), file=sys.stderr)
        return _EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
