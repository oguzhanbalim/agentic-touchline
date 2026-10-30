"""What a player's surroundings looked like when they received the ball.

Pure functions over a single 360 freeze frame. No database or I/O here, so every
branch can be tested against hand-built frames. Definitions are in docs/metrics.md.

Three facts about the data shape this module:

* Frames come from broadcast video. Not every player is visible, so "the nearest
  visible opponent" is an upper bound on the distance to the nearest opponent.
  ``visible_area`` lets us tell when that bound is tight.
* Frames carry no player identity beyond the actor, so lines are inferred from
  positions alone.
* Completed receptions are safe to use: across the five default matches all 3,721 frames
  place the actor within 5 yards of the event. Incomplete receipts are not (528 of 562
  place the actor far away, 295 of those at the mirrored point), so ingest drops them
  before they reach this module. The alignment and keeper checks here remain as guards,
  and they reject rather than repair: a flipped frame would invert every "goal-side" test.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from touchline.config import PITCH_LENGTH, PITCH_WIDTH
from touchline.schema import FramePlayer

# A frame is trusted only if its actor sits this close to the event location.
ALIGNMENT_TOLERANCE_YARDS = 5.0

# Between-the-lines parameters. These are conventions, not findings; see docs/metrics.md.
BETWEEN_LINES_MIN_X = 60.0  # the opposition half
BETWEEN_LINES_MAX_X = 102.0  # the edge of the penalty area
BETWEEN_LINES_CORRIDOR_YARDS = 20.0  # lateral reach of the local search
BETWEEN_LINES_DEPTH_YARDS = 15.0  # longitudinal reach of the local search
BETWEEN_LINES_MIN_EACH_SIDE = 2  # opponents needed goal-side and ball-side
MIN_VISIBLE_OUTFIELD_OPPONENTS = 7  # below this the line structure is not knowable

_DISC_SAMPLES = 72
_DISC_RINGS = (0.5, 1.0)
_PITCH_MIDLINE_X = PITCH_LENGTH / 2


class FrameStatus(StrEnum):
    OK = "ok"
    NO_FRAME = "no_frame"
    NO_ACTOR = "no_actor"
    MISALIGNED = "misaligned"
    MISORIENTED = "misoriented"


@dataclass(frozen=True)
class ReceptionContext:
    status: FrameStatus
    space_yards: float | None = None
    space_censored: bool = False
    visible_outfield_opponents: int = 0
    between_lines: bool | None = None


Point = tuple[float, float]


def _xy(player: FramePlayer) -> Point:
    return player.location[0], player.location[1]


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Ray-casting test. Points exactly on an edge may fall either way."""
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def visible_polygon(flat: Sequence[float]) -> list[Point]:
    """Convert the flat [x1, y1, ..., xn, yn, x1, y1] loop into points, dropping the repeat."""
    if len(flat) < 8 or len(flat) % 2:
        return []
    points = [(flat[index], flat[index + 1]) for index in range(0, len(flat), 2)]
    return points[:-1] if points[0] == points[-1] else points


def disc_is_visible(centre: Point, radius: float, polygon: Sequence[Point]) -> bool:
    """Whether the on-pitch part of a disc lies inside the visible area.

    Off-pitch sample points are ignored: no opponent is standing there, so the camera
    missing them does not hide anyone. Sampling two rings is enough because the camera
    polygon is close to convex.
    """
    if not polygon or not point_in_polygon(centre, polygon):
        return False
    for ring in _DISC_RINGS:
        for step in range(_DISC_SAMPLES):
            angle = 2 * math.pi * step / _DISC_SAMPLES
            sample = (
                centre[0] + ring * radius * math.cos(angle),
                centre[1] + ring * radius * math.sin(angle),
            )
            on_pitch = 0 <= sample[0] <= PITCH_LENGTH and 0 <= sample[1] <= PITCH_WIDTH
            if on_pitch and not point_in_polygon(sample, polygon):
                return False
    return True


def classify_frame(event_location: Point, players: Sequence[FramePlayer]) -> FrameStatus:
    """Decide whether a frame can be trusted for this event."""
    actors = [player for player in players if player.actor]
    if not actors:
        return FrameStatus.NO_ACTOR
    if math.dist(event_location, _xy(actors[0])) > ALIGNMENT_TOLERANCE_YARDS:
        return FrameStatus.MISALIGNED
    for player in players:
        if not player.keeper:
            continue
        # The actor's team attacks towards x = 120, so its keeper belongs in the low half.
        in_low_half = player.location[0] < _PITCH_MIDLINE_X
        if player.teammate != in_low_half:
            return FrameStatus.MISORIENTED
    return FrameStatus.OK


def is_between_lines(receiver: Point, opponents: Sequence[Point]) -> bool:
    """Apply the local between-the-lines test to outfield opponents.

    Looks for opponents goal-side and ball-side of the receiver, within a corridor and
    depth around them, in the part of the opposition half before the penalty area.
    """
    x, y = receiver
    if not BETWEEN_LINES_MIN_X <= x < BETWEEN_LINES_MAX_X:
        return False
    nearby = [
        opponent
        for opponent in opponents
        if abs(opponent[1] - y) <= BETWEEN_LINES_CORRIDOR_YARDS
        and abs(opponent[0] - x) <= BETWEEN_LINES_DEPTH_YARDS
    ]
    goal_side = sum(opponent[0] > x for opponent in nearby)
    ball_side = sum(opponent[0] < x for opponent in nearby)
    return goal_side >= BETWEEN_LINES_MIN_EACH_SIDE and ball_side >= BETWEEN_LINES_MIN_EACH_SIDE


def assess_reception(
    event_location: Point,
    players: Sequence[FramePlayer] | None,
    visible_area: Sequence[float],
) -> ReceptionContext:
    """Compute space and between-the-lines context for one reception."""
    if players is None:
        return ReceptionContext(FrameStatus.NO_FRAME)
    status = classify_frame(event_location, players)
    if status is not FrameStatus.OK:
        return ReceptionContext(status)

    receiver = _xy(next(player for player in players if player.actor))
    opponents = [player for player in players if not player.teammate]
    outfield = [_xy(player) for player in opponents if not player.keeper]

    polygon = visible_polygon(visible_area)
    distances = [math.dist(receiver, _xy(player)) for player in opponents]
    space: float | None = min(distances) if distances else None
    censored = space is None or not disc_is_visible(receiver, space, polygon)

    assessable = len(outfield) >= MIN_VISIBLE_OUTFIELD_OPPONENTS
    return ReceptionContext(
        status=FrameStatus.OK,
        space_yards=None if censored else space,
        space_censored=censored,
        visible_outfield_opponents=len(outfield),
        between_lines=is_between_lines(receiver, outfield) if assessable else None,
    )
