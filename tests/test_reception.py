"""Space and between-the-lines logic on hand-built frames."""

from __future__ import annotations

import pytest

from touchline.reception import (
    FrameStatus,
    assess_reception,
    classify_frame,
    disc_is_visible,
    is_between_lines,
    point_in_polygon,
    visible_polygon,
)
from touchline.schema import FramePlayer

WHOLE_PITCH = [0.0, 0.0, 120.0, 0.0, 120.0, 80.0, 0.0, 80.0, 0.0, 0.0]


def player(x: float, y: float, teammate: bool = False, actor: bool = False, keeper: bool = False):
    return FramePlayer(teammate=teammate, actor=actor, keeper=keeper, location=[x, y])


def test_visible_polygon_drops_the_closing_point() -> None:
    assert visible_polygon(WHOLE_PITCH) == [(0, 0), (120, 0), (120, 80), (0, 80)]


@pytest.mark.parametrize("flat", [[], [1.0, 2.0], [1.0, 2.0, 3.0]])
def test_visible_polygon_rejects_unusable_input(flat: list[float]) -> None:
    assert visible_polygon(flat) == []


def test_point_in_polygon() -> None:
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert point_in_polygon((5, 5), square)
    assert not point_in_polygon((15, 5), square)


def test_disc_visibility_needs_the_whole_on_pitch_disc() -> None:
    area = visible_polygon([50.0, 0.0, 70.0, 0.0, 70.0, 80.0, 50.0, 80.0, 50.0, 0.0])
    assert disc_is_visible((60, 40), 5, area)
    assert not disc_is_visible((60, 40), 15, area)


def test_disc_visibility_ignores_the_part_beyond_the_touchline() -> None:
    # The disc spills over y = 0, but no opponent stands off the pitch.
    area = visible_polygon([50.0, 0.0, 70.0, 0.0, 70.0, 40.0, 50.0, 40.0, 50.0, 0.0])
    assert disc_is_visible((60, 3), 6, area)


def test_frame_without_actor_is_unusable() -> None:
    assert classify_frame((90, 40), [player(95, 40)]) is FrameStatus.NO_ACTOR


def test_frame_with_distant_actor_is_rejected() -> None:
    frame = [player(30, 40, teammate=True, actor=True)]
    assert classify_frame((90, 40), frame) is FrameStatus.MISALIGNED


def test_frame_with_keepers_in_wrong_halves_is_rejected() -> None:
    actor = player(62, 40, teammate=True, actor=True)
    opposing_keeper_in_low_half = player(5, 40, keeper=True)
    assert classify_frame((62, 40), [actor, opposing_keeper_in_low_half]) is FrameStatus.MISORIENTED
    own_keeper_in_high_half = player(115, 40, teammate=True, keeper=True)
    assert classify_frame((62, 40), [actor, own_keeper_in_high_half]) is FrameStatus.MISORIENTED


def test_correct_keepers_pass() -> None:
    frame = [
        player(62, 40, teammate=True, actor=True),
        player(5, 40, teammate=True, keeper=True),
        player(115, 40, keeper=True),
    ]
    assert classify_frame((62, 40), frame) is FrameStatus.OK


def test_between_lines_needs_opponents_on_both_sides() -> None:
    receiver = (90.0, 40.0)
    both = [(97, 40), (100, 45), (85, 32), (82, 50)]
    assert is_between_lines(receiver, both)
    assert not is_between_lines(receiver, [(97, 40), (100, 45), (85, 32)])
    assert not is_between_lines(receiver, [(97, 40), (85, 32), (82, 50)])


def test_between_lines_ignores_far_opponents_and_wrong_areas() -> None:
    receiver = (90.0, 40.0)
    too_wide = [(97, 75), (100, 75), (85, 5), (82, 5)]
    assert not is_between_lines(receiver, too_wide)
    too_deep = [(110, 40), (112, 45), (60, 32), (62, 50)]
    assert not is_between_lines(receiver, too_deep)
    surrounded = [(1 + x, y) for x, y in [(50, 40), (50, 45), (40, 32), (40, 50)]]
    assert not is_between_lines((45.0, 40.0), surrounded), "own half"
    assert not is_between_lines((105.0, 40.0), [(110, 40), (112, 45), (100, 32), (98, 50)]), "box"


def eight_opponents() -> list[FramePlayer]:
    return [
        player(x, y)
        for x, y in [
            (95, 40), (100, 45), (85, 32), (82, 50), (70, 10), (70, 70), (112, 20), (112, 60),
        ]
    ]  # fmt: skip


def test_assess_full_reception() -> None:
    actor = player(90, 40, teammate=True, actor=True)
    context = assess_reception((90, 40), [actor, *eight_opponents()], WHOLE_PITCH)
    assert context.status is FrameStatus.OK
    assert context.space_yards == pytest.approx(5.0)
    assert not context.space_censored
    assert context.visible_outfield_opponents == 8
    assert context.between_lines is True


def test_censored_when_nearest_opponent_might_be_out_of_shot() -> None:
    actor = player(90, 40, teammate=True, actor=True)
    tiny = [88.0, 38.0, 92.0, 38.0, 92.0, 42.0, 88.0, 42.0, 88.0, 38.0]
    context = assess_reception((90, 40), [actor, *eight_opponents()], tiny)
    assert context.space_yards is None
    assert context.space_censored


def test_empty_visible_area_censors_space_but_keeps_line_check() -> None:
    actor = player(90, 40, teammate=True, actor=True)
    context = assess_reception((90, 40), [actor, *eight_opponents()], [])
    assert context.space_censored
    assert context.between_lines is True


def test_no_visible_opponents_is_censored() -> None:
    actor = player(90, 40, teammate=True, actor=True)
    context = assess_reception((90, 40), [actor], WHOLE_PITCH)
    assert context.space_yards is None
    assert context.space_censored
    assert context.between_lines is None


def test_too_few_visible_opponents_is_not_assessable() -> None:
    actor = player(90, 40, teammate=True, actor=True)
    context = assess_reception((90, 40), [actor, *eight_opponents()[:6]], WHOLE_PITCH)
    assert context.between_lines is None


def test_keeper_counts_for_space_but_not_for_lines() -> None:
    actor = player(100, 40, teammate=True, actor=True)
    keeper = player(103, 40, keeper=True)
    context = assess_reception((100, 40), [actor, keeper], WHOLE_PITCH)
    assert context.space_yards == pytest.approx(3.0)
    assert context.visible_outfield_opponents == 0


def test_no_frame() -> None:
    assert assess_reception((90, 40), None, []).status is FrameStatus.NO_FRAME
