"""Guard the repository against committing StatsBomb data."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


def tracked_files() -> list[str]:
    try:
        output = subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return output.splitlines()


def test_no_data_directory_or_database_is_tracked() -> None:
    for name in tracked_files():
        assert not name.startswith(("data/", "reports/")), name
        assert not name.endswith((".duckdb", ".duckdb.wal")), name


def test_no_json_files_are_tracked() -> None:
    """StatsBomb data is JSON. This repository has no reason to track any."""
    assert [name for name in tracked_files() if name.endswith(".json")] == []


def test_data_cache_is_ignored() -> None:
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in ignored.splitlines()


def test_no_large_files_are_tracked() -> None:
    too_big = [
        name
        for name in tracked_files()
        if (REPO / name).exists() and (REPO / name).stat().st_size > 400_000
    ]
    assert too_big == []
