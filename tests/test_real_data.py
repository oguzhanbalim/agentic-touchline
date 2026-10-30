"""Integrity and sanity checks on real StatsBomb data.

Marked `network`: run with `pytest -m network`. These download into the gitignored cache
and assert only facts and aggregates, so no StatsBomb data enters the repository.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from touchline.checks.faithfulness import check_faithfulness
from touchline.config import DEFAULT_COMPETITION_ID, DEFAULT_SEASON_ID, DEFAULT_TEAM, default_paths
from touchline.db import connect
from touchline.fetch import OpenDataCache
from touchline.ingest import check_integrity, ingest
from touchline.metrics import match_summary, pass_network, player_metrics
from touchline.metrics.player import find_players
from touchline.tools import ToolBox

pytestmark = pytest.mark.network

# The five matches of the default demo: id, home score, away score.
EXPECTED_RESULTS = {
    3938639: ("Turkey", 3, 1, "Georgia"),
    3930174: ("Turkey", 0, 3, "Portugal"),
    3930184: ("Czech Republic", 1, 2, "Turkey"),
    3941022: ("Austria", 1, 2, "Turkey"),
    3942382: ("Netherlands", 2, 1, "Turkey"),
}


CZECH_SENDING_OFF = (3930184, "Czech Republic")


@pytest.fixture(scope="module")
def real_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("real") / "real.duckdb"
    cache = OpenDataCache(default_paths().cache_dir)
    ingest(cache, db_path, DEFAULT_COMPETITION_ID, DEFAULT_SEASON_ID, DEFAULT_TEAM)
    return db_path


@pytest.fixture
def real_con(real_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = connect(real_db, read_only=True)
    yield connection
    connection.close()


def test_results_match_events(real_con: duckdb.DuckDBPyConnection) -> None:
    rows = real_con.execute(
        "SELECT match_id, home_team, home_score, away_score, away_team FROM matches"
    ).fetchall()
    assert {row[0]: row[1:] for row in rows} == EXPECTED_RESULTS
    for match_id in EXPECTED_RESULTS:
        assert check_integrity(real_con, match_id) == []


def test_goals_including_own_goals_match_the_score(real_con: duckdb.DuckDBPyConnection) -> None:
    for match_id, (home, home_score, away_score, away) in EXPECTED_RESULTS.items():
        teams = {team["team"]: team for team in match_summary(real_con, match_id)["teams"]}
        for team, score in ((home, home_score), (away, away_score)):
            counted = teams[team]["goals_from_shots"] + teams[team]["own_goals_received"]
            assert counted == score, f"match {match_id} {team}"


def test_team_minutes_equal_eleven_players_for_the_match(
    real_con: duckdb.DuckDBPyConnection,
) -> None:
    rows = real_con.execute(
        """
        WITH length AS (
            SELECT match_id, sum(end_clock - start_clock) AS seconds FROM (
                SELECT match_id, period, max(minute * 60 + second) AS end_clock,
                       CASE period WHEN 1 THEN 0 WHEN 2 THEN 2700 WHEN 3 THEN 5400 ELSE 6300 END
                           AS start_clock
                FROM events WHERE period <= 4 GROUP BY 1, 2
            ) GROUP BY 1
        )
        SELECT m.match_id, m.team, sum(m.minutes), any_value(l.seconds) / 60.0
        FROM player_minutes m JOIN length l USING (match_id) GROUP BY 1, 2
        """
    ).fetchall()
    assert len(rows) == 10
    for match_id, team, minutes, match_length in rows:
        lost = 0.0
        if (match_id, team) == CZECH_SENDING_OFF:
            # Second yellow at 19:17: ten players for the rest of the match, plus the short
            # treatment gaps that the stints record.
            lost = match_length - 19.28
        # Slack covers treatment gaps and clock rounding between stints and events.
        assert minutes == pytest.approx(11 * match_length - lost, abs=3.0), f"{match_id} {team}"


def test_no_empty_minute_rows(real_con: duckdb.DuckDBPyConnection) -> None:
    rows = real_con.execute("SELECT count(*) FROM player_minutes WHERE minutes <= 0").fetchone()
    assert rows == (0,)


def test_a_real_sending_off_caps_minutes_and_ends_the_network_window(
    real_con: duckdb.DuckDBPyConnection,
) -> None:
    match_id, team = CZECH_SENDING_OFF
    assert pass_network(real_con, match_id, team).window_end_minute == 19
    player_id = find_players(real_con, "barak")[0]["player_id"]
    assert player_metrics(real_con, player_id, match_id)["minutes"] == pytest.approx(19.3)


def test_left_means_low_y(real_con: duckdb.DuckDBPyConnection) -> None:
    rows = dict(
        real_con.execute(
            "SELECT position, avg(y) FROM events "
            "WHERE position IN ('Left Back', 'Right Back') GROUP BY 1"
        ).fetchall()
    )
    assert rows["Left Back"] < 25 < 55 < rows["Right Back"]


def test_every_completed_reception_frame_lines_up_with_its_event(
    real_con: duckdb.DuckDBPyConnection,
) -> None:
    statuses = dict(
        real_con.execute("SELECT frame_status, count(*) FROM receptions GROUP BY 1").fetchall()
    )
    assert statuses.get("misaligned", 0) == 0
    assert statuses.get("no_actor", 0) == 0
    assert statuses.get("misoriented", 0) < 0.01 * sum(statuses.values())
    assert statuses["ok"] > 0.8 * sum(statuses.values())


def _rate_by_role(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[float, float]]:
    rows = con.execute(
        """
        SELECT CASE
                 WHEN e.position LIKE '%Center Back' THEN 'centre_back'
                 WHEN e.position LIKE '%Forward' THEN 'striker'
                 WHEN e.position IN ('Left Wing', 'Right Wing') THEN 'winger'
               END AS role,
               1.0 * count(*) FILTER (r.between_lines) / count(r.between_lines) AS rate,
               median(r.space_yards) AS space
        FROM receptions r JOIN events e USING (event_id)
        WHERE r.open_play GROUP BY 1 HAVING role IS NOT NULL
        """
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_between_the_lines_separates_roles_the_expected_way(
    real_con: duckdb.DuckDBPyConnection,
) -> None:
    rates = _rate_by_role(real_con)
    assert rates["centre_back"][0] < 0.05
    assert rates["striker"][0] > 0.2
    assert rates["winger"][0] > 0.2


def test_centre_backs_receive_with_more_space_than_strikers(
    real_con: duckdb.DuckDBPyConnection,
) -> None:
    rates = _rate_by_role(real_con)
    assert rates["centre_back"][1] > rates["striker"][1]


def test_pass_network_has_the_starting_eleven(real_con: duckdb.DuckDBPyConnection) -> None:
    network = pass_network(real_con, 3938639, "Turkey")
    assert len(network.nodes) == 11
    assert network.window_end_minute is not None and network.window_end_minute > 45


def test_faithfulness_on_real_tool_output(real_con: duckdb.DuckDBPyConnection) -> None:
    toolbox = ToolBox(real_con)
    player_id = find_players(real_con, "arda guler")[0]["player_id"]
    metrics = toolbox.run("get_player_metrics", {"player_id": player_id, "match_id": 3938639})
    assert not metrics.is_error
    passes = metrics.output["passes_completed"]
    pct = str(metrics.output["pass_completion_pct"])
    assert check_faithfulness(f"{passes} completed passes, {pct}%", toolbox.evidence).passed
    assert check_faithfulness(f"%{pct.replace('.', ',')} tamamlama", toolbox.evidence).passed
    assert not check_faithfulness(f"{passes + 1000} completed passes", toolbox.evidence).passed


def test_named_scorer_has_the_goal_in_the_data(real_con: duckdb.DuckDBPyConnection) -> None:
    player_id = find_players(real_con, "arda guler")[0]["player_id"]
    metrics = player_metrics(real_con, player_id, 3938639)
    assert metrics["goals"] == 1
    assert metrics["shots"] >= 1
