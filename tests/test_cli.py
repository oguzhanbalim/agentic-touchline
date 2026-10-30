"""The command line, on the invented match."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakes import ScriptedClient, message, text, tool_use
from synthetic import FORWARD_A, MATCH_ID
from touchline import cli


@pytest.fixture
def data_dir(synthetic_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TOUCHLINE_DATA_DIR", str(synthetic_db_path.parent))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return synthetic_db_path.parent


def run(*args: str) -> int:
    return cli.main(list(args))


def test_metrics_only_report_needs_no_key(data_dir: Path, tmp_path: Path, capsys) -> None:
    code = run(
        "report", "--match", str(MATCH_ID), "--player", "Alpha Şükrü Forward",
        "--metrics-only", "--out", str(tmp_path),
    )  # fmt: skip
    output = capsys.readouterr().out
    assert code == 0
    assert "| Shots / goals / xG | 2 / 1 / 0.4 |" in output
    assert "Data: StatsBomb Open Data" in output
    charts = list(tmp_path.rglob("*.png"))
    assert len(charts) == 4


def test_report_on_a_player_outside_the_match_fails_clearly(data_dir: Path, capsys) -> None:
    assert run("report", "--match", "999", "--player", "alpha keeper", "--metrics-only") == 2
    assert "match 999" in capsys.readouterr().err


def test_player_lookup_lists_ambiguous_names(data_dir: Path) -> None:
    with pytest.raises(SystemExit, match="more than one"):
        run("report", "--match", str(MATCH_ID), "--player", "alpha", "--metrics-only")


def test_unknown_player_is_a_clear_error(data_dir: Path) -> None:
    with pytest.raises(SystemExit, match="No player"):
        run("report", "--match", str(MATCH_ID), "--player", "nobody", "--metrics-only")


def test_no_database_is_a_clear_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TOUCHLINE_DATA_DIR", str(tmp_path))
    assert run("chat", "--player", "x", "--ask", "y") == 2
    assert "touchline ingest" in capsys.readouterr().err


def test_chat_without_credentials_explains_itself(data_dir: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(data_dir))
    code = run("chat", "--player", "alpha keeper", "--ask", "Where do I receive the ball?")
    assert code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_chat_referral_works_without_credentials(data_dir: Path, capsys) -> None:
    assert run("chat", "--player", "alpha keeper", "--ask", "Dizim ağrıyor.") == 0
    assert "doktoru" in capsys.readouterr().out


def test_report_then_approve(data_dir: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    report = {
        "headline": "One goal from 2 shots.",
        "role_in_the_match": "Forward.",
        "team_context": "A win.",
        "findings": [],
        "where_to_receive": [],
        "limitations": [],
        "questions_for_staff": [],
    }
    arguments = {"player_id": FORWARD_A, "match_id": MATCH_ID}
    client = ScriptedClient(
        [
            message([tool_use("get_player_metrics", arguments)], "tool_use"),
            message([text(json.dumps(report))]),
        ]
    )
    monkeypatch.setattr(cli, "_build_client", lambda: client)
    code = run(
        "report", "--match", str(MATCH_ID), "--player", "Alpha Şükrü Forward",
        "--out", str(tmp_path),
    )  # fmt: skip
    output = capsys.readouterr().out
    assert code == 0
    assert "It is not released" in output
    draft = next(tmp_path.rglob("draft.json"))
    assert not list(tmp_path.rglob("report.md"))

    assert run("approve", str(draft), "--by", "A. Analyst") == 0
    assert list(tmp_path.rglob("report.md"))


def test_check_boundary_passes(capsys) -> None:
    assert run("check-boundary") == 0
    assert '"failed": 0' in capsys.readouterr().out
