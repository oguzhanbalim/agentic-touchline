"""Shared fixtures. The default database is built from the invented match in synthetic.py."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from synthetic import COMPETITION_ID, SEASON_ID, build_files
from touchline.db import connect
from touchline.fetch import OpenDataCache
from touchline.ingest import ingest


def _fake_downloader(url: str) -> bytes:
    files = build_files()
    if url not in files:
        raise FileNotFoundError(url)
    return files[url]


@pytest.fixture(scope="session")
def synthetic_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data_dir = tmp_path_factory.mktemp("touchline-data")
    db_path = data_dir / "touchline.duckdb"
    cache = OpenDataCache(data_dir / "cache", downloader=_fake_downloader)
    ingest(cache, db_path, COMPETITION_ID, SEASON_ID, team=None)
    return db_path


@pytest.fixture
def con(synthetic_db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = connect(synthetic_db_path, read_only=True)
    yield connection
    connection.close()
