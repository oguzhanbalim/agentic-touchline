"""Ingest, schema validation and integrity checks, on the invented match."""

from __future__ import annotations

import json
from pathlib import Path

import pydantic
import pytest

from synthetic import ALPHA, DEFENDER_B, MATCH_ID, MID_A, SUB_A, build_events, build_files
from touchline.db import connect
from touchline.fetch import OpenDataCache
from touchline.ingest import IntegrityError, check_integrity, ingest
from touchline.schema import Event


def test_tables_are_populated(con) -> None:
    for table, expected in (("matches", 1), ("events", len(build_events())), ("frames", 4)):
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == expected


def test_integrity_passes_on_consistent_data(con) -> None:
    assert check_integrity(con, MATCH_ID) == []


def test_integrity_reports_unknown_match(con) -> None:
    assert check_integrity(con, 999) == ["match 999 is not in the matches table"]


def test_ingest_rejects_a_score_that_contradicts_the_events(tmp_path: Path) -> None:
    files = build_files()
    url = next(key for key in files if "/matches/" in key)
    matches = json.loads(files[url])
    matches[0]["home_score"] = 5
    files[url] = json.dumps(matches).encode()

    def downloader(target: str) -> bytes:
        if target not in files:
            raise FileNotFoundError(target)
        return files[target]

    cache = OpenDataCache(tmp_path / "cache", downloader=downloader)
    with pytest.raises(IntegrityError, match="events give 2 goals, result 5"):
        ingest(cache, tmp_path / "t.duckdb", 1, 1, team=None)


def test_schema_rejects_malformed_events() -> None:
    event = build_events()[0]
    event["location"] = ["not", "numbers"]
    with pytest.raises(pydantic.ValidationError):
        Event.model_validate(event)


def test_reception_filtering(con) -> None:
    rows = dict(con.execute("SELECT frame_status, count(*) FROM receptions GROUP BY 1").fetchall())
    # The failed receipt is dropped; the corner reception has no frame, like rec-no-frame.
    assert rows == {"ok": 2, "no_frame": 2, "misaligned": 1}


def test_set_piece_receptions_are_not_open_play(con) -> None:
    assert con.execute("SELECT count(*) FROM receptions WHERE NOT open_play").fetchone()[0] == 1


def test_minutes_handle_half_time_and_stoppage_clock(con) -> None:
    def minutes(player: int) -> dict[str, float]:
        rows = con.execute(
            "SELECT position, minutes FROM player_minutes WHERE player_id = ?", [player]
        ).fetchall()
        return dict(rows)

    # Period 1 runs to 46:00, and the "45:00 in period 2" stint end adds nothing to it.
    assert minutes(MID_A) == {"Center Midfield": 46.0, "Center Forward": 15.0}
    # On at 60:00 in period 2, on to the last event at 92:00.
    assert minutes(SUB_A) == {"Center Midfield": 32.0}


def test_a_sending_off_caps_minutes_even_if_a_stint_runs_on(con) -> None:
    rows = con.execute(
        "SELECT sum(minutes) FROM player_minutes WHERE player_id = ?", [DEFENDER_B]
    ).fetchone()
    # 46 in period 1, then 45:00 to 70:00.
    assert rows[0] == pytest.approx(71.0)


def test_a_team_plays_the_whole_match(con) -> None:
    total = con.execute(
        "SELECT sum(minutes) FROM player_minutes WHERE team = ? AND player_id IN (?, ?)",
        [ALPHA, MID_A, SUB_A],
    ).fetchone()[0]
    assert total == pytest.approx(46 + 47)


def test_downloads_are_cached(tmp_path: Path) -> None:
    calls: list[str] = []

    def downloader(url: str) -> bytes:
        calls.append(url)
        return b"[]"

    cache = OpenDataCache(tmp_path, downloader=downloader)
    assert cache.events(1) == []
    assert cache.events(1) == []
    assert len(calls) == 1


def test_a_corrupt_download_leaves_no_file(tmp_path: Path) -> None:
    cache = OpenDataCache(tmp_path, downloader=lambda url: b"{not json")
    with pytest.raises(json.JSONDecodeError):
        cache.events(1)
    assert not list(tmp_path.rglob("*.json"))


def test_missing_360_file_means_no_frames(tmp_path: Path) -> None:
    def downloader(url: str) -> bytes:
        raise FileNotFoundError(url)

    assert OpenDataCache(tmp_path, downloader=downloader).frames(1) == []


def test_read_only_connection_rejects_writes(synthetic_db_path: Path) -> None:
    connection = connect(synthetic_db_path, read_only=True)
    with pytest.raises(Exception, match="read-only"):
        connection.execute("DELETE FROM events")
    connection.close()
