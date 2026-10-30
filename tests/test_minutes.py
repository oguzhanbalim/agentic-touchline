"""Minutes from the event stream, including the clock overlaps the lineup files get wrong."""

from __future__ import annotations

import pytest

from synthetic import ALPHA, DEFENDER_A, FORWARD_A, KEEPER_A, MID_A, SUB_A, event, tactics
from touchline.minutes import minutes_from_events
from touchline.schema import Event


def _built(*events: dict) -> list[Event]:
    """Validate events, indexing them in match order whatever order they were created in."""
    return [
        Event.model_validate({**item, "index": index}) for index, item in enumerate(events, start=1)
    ]


def _totals(events: list[Event]) -> dict[int, float]:
    rows, _ = minutes_from_events(events)
    totals: dict[int, float] = {}
    for row in rows:
        totals[row.player_id] = totals.get(row.player_id, 0.0) + row.minutes
    return totals


XI = [
    (KEEPER_A, "Goalkeeper"),
    (DEFENDER_A, "Center Back"),
    (MID_A, "Center Midfield"),
    (FORWARD_A, "Center Forward"),
]


def _extra_time_match(*middle: dict) -> list[Event]:
    """45 + 3, 45 + 4, 15 + 1, 15 + 2 minutes, and a shootout: 125 minutes in all."""
    return _built(
        tactics("xi", "Starting XI", ALPHA, XI, minute=0),
        event("end-1", "Half End", ALPHA, minute=48, period=1),
        *middle,
        event("end-2", "Half End", ALPHA, minute=94, period=2),
        event("end-3", "Half End", ALPHA, minute=106, period=3),
        event("end-4", "Half End", ALPHA, minute=122, period=4),
        event("end-5", "Half End", ALPHA, minute=130, period=5),
    )


def test_extra_time_counts_every_period_and_not_the_shootout() -> None:
    assert _totals(_extra_time_match())[KEEPER_A] == pytest.approx(48 + 49 + 16 + 17)


def test_a_change_in_stoppage_time_is_not_confused_with_the_next_period() -> None:
    # A clock of 93:00 in period 2 is later than 90:00 in period 3 by clock alone, and it is
    # exactly this overlap that scrambles the lineup files.
    substitution = event(
        "sub", "Substitution", ALPHA, MID_A, "Center Midfield", minute=93, period=2,
        substitution={"replacement": {"id": SUB_A, "name": "Alpha Substitute"}},
    )  # fmt: skip
    totals = _totals(_extra_time_match(substitution))
    assert totals[MID_A] == pytest.approx(48 + 48)
    assert totals[SUB_A] == pytest.approx(1 + 16 + 17)
    assert totals[MID_A] + totals[SUB_A] == pytest.approx(totals[KEEPER_A])


def test_a_substitute_takes_the_position_on_the_event() -> None:
    substitution = event(
        "sub", "Substitution", ALPHA, MID_A, "Center Midfield", minute=93, period=2,
        substitution={"replacement": {"id": SUB_A, "name": "Alpha Substitute"}},
    )  # fmt: skip
    rows, _ = minutes_from_events(_extra_time_match(substitution))
    assert {row.position for row in rows if row.player_id == SUB_A} == {"Center Midfield"}


def test_a_position_change_splits_time_between_positions() -> None:
    moved = [(player, "Center Back" if player == MID_A else role) for player, role in XI]
    shift = tactics("shift", "Tactical Shift", ALPHA, moved, minute=60, period=2)
    rows, _ = minutes_from_events(_extra_time_match(shift))
    by_position = {row.position: row.minutes for row in rows if row.player_id == MID_A}
    assert by_position["Center Midfield"] == pytest.approx(48 + 15)
    assert by_position["Center Back"] == pytest.approx(34 + 16 + 17)


def test_a_sending_off_stops_the_clock_and_is_recorded() -> None:
    card = event(
        "card", "Foul Committed", ALPHA, MID_A, "Center Midfield", [50, 40], minute=60, period=2,
        foul_committed={"card": {"id": 1, "name": "Second Yellow"}},
    )  # fmt: skip
    events = _extra_time_match(card)
    assert _totals(events)[MID_A] == pytest.approx(48 + 15)
    _, sendings_off = minutes_from_events(events)
    assert [(item.player_id, item.minute) for item in sendings_off] == [(MID_A, 60)]


def test_a_later_tactical_shift_cannot_bring_back_a_player_sent_off() -> None:
    # Real data does this: a shift two minutes after a second yellow still lists the player.
    card = event(
        "card", "Foul Committed", ALPHA, MID_A, "Center Midfield", [50, 40], minute=19, period=1,
        foul_committed={"card": {"id": 1, "name": "Second Yellow"}},
    )  # fmt: skip
    shift = tactics("shift", "Tactical Shift", ALPHA, XI, minute=21, period=1)
    totals = _totals(_extra_time_match(card, shift))
    assert totals[MID_A] == pytest.approx(19)


def test_a_yellow_card_changes_nothing() -> None:
    card = event(
        "card", "Foul Committed", ALPHA, MID_A, "Center Midfield", [50, 40], minute=60, period=2,
        foul_committed={"card": {"id": 1, "name": "Yellow Card"}},
    )  # fmt: skip
    events = _extra_time_match(card)
    assert minutes_from_events(events)[1] == []
    assert _totals(events)[MID_A] == _totals(events)[KEEPER_A]


def test_treatment_off_the_pitch_counts_as_played() -> None:
    off = event("off", "Player Off", ALPHA, MID_A, "Center Midfield", minute=60, period=2)
    on = event("on", "Player On", ALPHA, MID_A, "Center Midfield", minute=61, period=2)
    events = _extra_time_match(off, on)
    assert _totals(events)[MID_A] == _totals(events)[KEEPER_A]
