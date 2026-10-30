"""Metrics against values worked out by hand from the invented match in synthetic.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from synthetic import (
    ALPHA,
    BETA,
    DEFENDER_A,
    DEFENDER_B,
    FORWARD_A,
    KEEPER_A,
    MATCH_ID,
    MID_A,
    MID_B,
)
from touchline.metrics import (
    METRICS,
    match_summary,
    pass_network,
    player_metrics,
    player_shots,
    reception_zones,
    role_comparison,
)
from touchline.metrics.definitions import render_definitions_markdown
from touchline.metrics.network import player_pass_links
from touchline.metrics.player import ROLE_GROUPS, find_players, fold_name, primary_role
from touchline.metrics.team import UnknownEntityError, list_matches


def test_match_summary_totals(con) -> None:
    summary = match_summary(con, MATCH_ID)
    assert (summary["home_score"], summary["away_score"]) == (2, 1)
    alpha = next(team for team in summary["teams"] if team["team"] == ALPHA)
    assert alpha["shots"] == 2
    assert alpha["goals_from_shots"] == 1
    assert alpha["own_goals_received"] == 1
    assert alpha["xg"] == pytest.approx(0.40)


def test_shootout_is_excluded(con) -> None:
    metrics = player_metrics(con, FORWARD_A, MATCH_ID)
    assert metrics["shots"] == 2
    assert metrics["goals"] == 1
    assert metrics["xg"] == pytest.approx(0.40)
    assert all(shot["minute"] < 120 for shot in player_shots(con, FORWARD_A)["shots"])


def test_progressive_passes_definition(con) -> None:
    metrics = player_metrics(con, MID_A, MATCH_ID)
    assert metrics["passes_attempted"] == 7
    assert metrics["passes_completed"] == 6
    assert metrics["open_play_passes_attempted"] == 6
    assert metrics["open_play_passes_completed"] == 5
    # prog-clear and prog-boundary only. See the labelled cases in synthetic.py.
    assert metrics["progressive_passes"] == 2


def test_progressive_carries_definition(con) -> None:
    metrics = player_metrics(con, MID_A, MATCH_ID)
    assert metrics["carries"] == 2
    assert metrics["progressive_carries"] == 1


def test_pressures_by_third(con) -> None:
    metrics = player_metrics(con, MID_B, MATCH_ID)
    assert metrics["pressures"] == 3
    assert metrics["pressures_defensive_third"] == 1
    assert metrics["pressures_middle_third"] == 1
    assert metrics["pressures_attacking_third"] == 1


def test_per_90_uses_minutes_played(con) -> None:
    metrics = player_metrics(con, MID_A, MATCH_ID)
    assert metrics["minutes"] == 61.0
    assert metrics["per_90"]["progressive_passes"] == pytest.approx(2 * 90 / 61, abs=0.01)


def test_reception_summary(con) -> None:
    receptions = player_metrics(con, FORWARD_A, MATCH_ID)["receptions"]
    # Open-play: between, censored, no-frame, misaligned. The corner and failed one are out.
    assert receptions["open_play_receptions"] == 4
    assert receptions["with_usable_frame"] == 2
    assert receptions["space_measured"] == 1
    assert receptions["space_censored"] == 1
    assert receptions["median_space_yards"] == 5.0
    assert receptions["between_lines"] == 2
    assert receptions["between_lines_assessable"] == 2
    assert receptions["between_lines_pct"] == 100.0


def test_pass_network_window_and_links(con) -> None:
    network = pass_network(con, MATCH_ID, ALPHA)
    assert network.window_end_minute == 60
    links = {(link["player_a"], link["player_b"]): link["passes"] for link in network.links}
    # Five completed open-play passes Mid to Forward before the sub (the corner and the
    # post-substitution pass are out), one Keeper to Defender, one Defender to Mid.
    assert links == {(MID_A, FORWARD_A): 5, (KEEPER_A, DEFENDER_A): 1, (DEFENDER_A, MID_A): 1}


def test_pass_network_window_ends_at_a_sending_off(con) -> None:
    assert pass_network(con, MATCH_ID, BETA).window_end_minute == 70


def test_a_card_for_a_player_off_the_pitch_changes_nothing(con) -> None:
    # The midfielder left at minute 60 and is "shown a red" at minute 91 in the fixture.
    rows = con.execute("SELECT player_id FROM sendings_off").fetchall()
    assert rows == [(DEFENDER_B,)]


def test_pass_network_average_position(con) -> None:
    network = pass_network(con, MATCH_ID, ALPHA)
    keeper = next(node for node in network.nodes if node["player_id"] == KEEPER_A)
    assert (keeper["avg_x"], keeper["avg_y"]) == (6.0, 40.0)
    assert keeper["touches"] == 1


def test_pass_network_rejects_a_team_not_in_the_match(con) -> None:
    with pytest.raises(UnknownEntityError):
        pass_network(con, MATCH_ID, "Nobody")


def test_pass_links_count_both_directions(con) -> None:
    links = player_pass_links(con, MATCH_ID, MID_A)["partners"]
    by_name = {link["partner"]: link for link in links}
    assert by_name["Alpha Defender"] == {
        "partner": "Alpha Defender",
        "passes_to_partner": 0,
        "passes_from_partner": 1,
    }
    assert by_name["Alpha Şükrü Forward"]["passes_to_partner"] == 5


def test_reception_zones(con) -> None:
    zones = reception_zones(con, FORWARD_A, MATCH_ID)
    assert zones["open_play_receptions"] == 4
    only = zones["zones"][0]
    assert only["zone"] == "final_third:centre"
    assert only["receptions"] == 4
    assert only["share_pct"] == 100.0
    # One measured frame is below the sample floor, so no median is reported.
    assert only["median_space_yards"] is None


def test_primary_role_follows_minutes(con) -> None:
    assert primary_role(con, MID_A) == "central_midfield"
    assert primary_role(con, FORWARD_A) == "striker"


def test_role_comparison_ranks_against_peers(con) -> None:
    comparison = role_comparison(con, MID_B, MATCH_ID, min_minutes=30)
    assert comparison["role"] == "central_midfield"
    # Alpha's midfielder (46 minutes) and the substitute (32 minutes) are peers.
    assert comparison["peers"] == 2
    pressures = next(item for item in comparison["metrics"] if item["metric"] == "pressures")
    assert pressures["rank_including_player"] == 1
    assert pressures["peer_median"] == 0.0


def test_role_comparison_respects_minimum_minutes(con) -> None:
    comparison = role_comparison(con, MID_B, MATCH_ID, min_minutes=40)
    assert comparison["peers"] == 1


def test_unknown_ids_raise_a_clear_error(con) -> None:
    with pytest.raises(UnknownEntityError, match="player 1 is not"):
        player_metrics(con, 1)
    with pytest.raises(UnknownEntityError, match="match 1 is not"):
        match_summary(con, 1)


def test_find_players_ignores_diacritics_and_case(con) -> None:
    assert [row["player"] for row in find_players(con, "SUKRU")] == ["Alpha Şükrü Forward"]
    assert find_players(con, "") == []
    assert find_players(con, "nobody") == []


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("İsmail Yüksek", "ismail yuksek"),
        ("Barış  Yılmaz", "baris yilmaz"),
        ("Çağlar", "caglar"),
    ],
)
def test_fold_name(raw: str, folded: str) -> None:
    assert fold_name(raw) == folded


def test_list_matches(con) -> None:
    assert [match["match_id"] for match in list_matches(con)] == [MATCH_ID]


def test_every_statsbomb_position_has_one_role() -> None:
    # Position labels from the StatsBomb Open Data Events specification.
    labels = {
        "Goalkeeper", "Right Back", "Right Center Back", "Center Back", "Left Center Back",
        "Left Back", "Right Wing Back", "Left Wing Back", "Right Defensive Midfield",
        "Center Defensive Midfield", "Left Defensive Midfield", "Right Midfield",
        "Right Center Midfield", "Center Midfield", "Left Center Midfield", "Left Midfield",
        "Right Wing", "Right Attacking Midfield", "Center Attacking Midfield",
        "Left Attacking Midfield", "Left Wing", "Right Center Forward", "Center Forward",
        "Left Center Forward", "Secondary Striker",
    }  # fmt: skip
    grouped = [position for positions in ROLE_GROUPS.values() for position in positions]
    assert sorted(grouped) == sorted(labels)


def test_every_metric_states_definition_and_limitations() -> None:
    assert len(METRICS) >= 9
    for metric in METRICS.values():
        assert metric.definition and metric.limitations


def test_metric_doc_is_in_sync_with_registry() -> None:
    doc = Path(__file__).parent.parent / "docs" / "metrics.md"
    assert doc.read_text(encoding="utf-8") == render_definitions_markdown(), (
        "run `python -m touchline.metrics.docs`"
    )
