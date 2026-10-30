"""The definition and known limitations of every metric, in one place.

The same text feeds docs/metrics.md, the agents' system prompt and the
`get_metric_definition` tool, so what the code does and what the model is told cannot
drift apart. Run `python -m touchline.metrics.docs` to regenerate the document.
"""

from __future__ import annotations

from dataclasses import dataclass

from touchline.reception import (
    ALIGNMENT_TOLERANCE_YARDS,
    BETWEEN_LINES_CORRIDOR_YARDS,
    BETWEEN_LINES_DEPTH_YARDS,
    BETWEEN_LINES_MAX_X,
    BETWEEN_LINES_MIN_EACH_SIDE,
    BETWEEN_LINES_MIN_X,
    MIN_VISIBLE_OUTFIELD_OPPONENTS,
)

SET_PIECE_PASS_TYPES = frozenset({"Corner", "Free Kick", "Goal Kick", "Kick Off", "Throw-in"})

# Progressive actions: the end point is at least this much closer to the goal centre ...
PROGRESSIVE_MIN_RELATIVE_GAIN = 0.25
# ... and at least this many yards closer, so tiny touches near goal do not count.
PROGRESSIVE_MIN_ABSOLUTE_GAIN_YARDS = 5.0

MIN_SAMPLE_FOR_MEDIAN = 5
DEFAULT_PEER_MIN_MINUTES = 45.0


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    definition: str
    limitations: str


_DEFINITIONS = (
    MetricDefinition(
        "shots_and_xg",
        "Shot events in periods 1 to 4, excluding penalty shootouts. xG is StatsBomb's own "
        "`statsbomb_xg` field, summed. A goal is a shot with outcome Goal.",
        "xG is StatsBomb's model and is not recomputed or validated here. Own goals are "
        "credited to the team in match scores and to no player. Small samples: one match "
        "holds very few shots per player.",
    ),
    MetricDefinition(
        "passes",
        "Pass events by the player. Attempted counts every pass. Completed means the pass has "
        "no outcome, which is how StatsBomb encodes completion. Open play excludes corners, "
        "free kicks, goal kicks, kick-offs and throw-ins.",
        "Completion rate does not account for pass difficulty, so it is not a measure of "
        "passing quality on its own.",
    ),
    MetricDefinition(
        "progressive_passes",
        "Completed open-play passes whose end point is at least "
        f"{PROGRESSIVE_MIN_RELATIVE_GAIN:.0%} closer to the centre of the opposition goal "
        f"(120, 40) than the start point, and at least {PROGRESSIVE_MIN_ABSOLUTE_GAIN_YARDS:.0f} "
        "yards closer.",
        "The thresholds are a convention chosen for this project; other providers use other "
        "cut-offs, so counts are not comparable across sources. It ignores whether a pass "
        "beat any opponent. Pitch units are StatsBomb's 120 x 80 scale, nominally yards.",
    ),
    MetricDefinition(
        "progressive_carries",
        "Carry events meeting the same distance test as progressive passes, using the carry's "
        "start and end location.",
        "Same threshold caveats as progressive passes. StatsBomb's carries are derived between "
        "other on-ball events, so very short movements are not separate actions.",
    ),
    MetricDefinition(
        "pressures",
        "Pressure events by the player. StatsBomb records one when a player applies pressure "
        "to an opponent receiving, carrying or releasing the ball. Thirds use the presser's "
        "own attacking direction: defensive below x = 40, attacking at x = 80 and above.",
        "Counts depend on how much the opponent has the ball, so a higher count can mean more "
        "time defending as well as more pressing intent. Whether the pressure succeeded is "
        "not measured.",
    ),
    MetricDefinition(
        "pass_network",
        "For one team, from kick-off until its first substitution or player sent off: completed "
        "open-play passes between each pair, in both directions together. A player's average "
        "position is the mean of the locations at which they made and received those passes.",
        "It covers only the window before the first change, which can be short. An average "
        "position can sit where the player rarely stood, and carries and off-ball movement do "
        "not move it.",
    ),
    MetricDefinition(
        "space_at_reception",
        "For a completed open-play reception with a usable 360 frame: distance in yards from "
        "the receiver to the nearest visible opponent, goalkeeper included. A frame is usable "
        f"if the actor is within {ALIGNMENT_TOLERANCE_YARDS:.0f} yards of the event location and "
        "no visible goalkeeper is in the wrong half. A value is censored, and left out of the "
        "median, if the disc of that radius around the receiver is not fully inside the "
        "camera's visible area (off-pitch parts ignored), because a closer opponent could be "
        "out of shot.",
        "Dropping censored frames biases the median down: wide-open receptions are the most "
        "likely to be censored, so the reported median understates space. The censored count "
        "is always reported beside it. Positions come from broadcast video and are estimates. "
        "It is a snapshot at the moment of reception and ignores opponents' speed and direction.",
    ),
    MetricDefinition(
        "receptions_between_lines",
        "A heuristic on completed open-play receptions with a usable frame showing at least "
        f"{MIN_VISIBLE_OUTFIELD_OPPONENTS} outfield opponents (assessable receptions). A "
        f"reception counts when the receiver is between x = {BETWEEN_LINES_MIN_X:.0f} and x = "
        f"{BETWEEN_LINES_MAX_X:.0f}, and at least {BETWEEN_LINES_MIN_EACH_SIDE} outfield opponents "
        f"are goal-side and at least {BETWEEN_LINES_MIN_EACH_SIDE} ball-side, all within "
        f"{BETWEEN_LINES_CORRIDOR_YARDS:.0f} yards laterally and {BETWEEN_LINES_DEPTH_YARDS:.0f} "
        "yards longitudinally of the receiver.",
        "This approximates the idea; it has not been validated against human labels. Frames "
        "have no player identities, so it cannot tell a midfield line from a pair of retreating "
        "defenders, and it does not check that the opponents form a line. The parameters are a "
        "judgement call. Use it to compare roles and matches, not as an exact count.",
    ),
    MetricDefinition(
        "reception_zones",
        "Completed open-play receptions grouped into 15 zones: thirds at x = 40 and x = 80; "
        "channels at y = 18, 30, 50 and 62, taken from the penalty-area and six-yard-box lines. "
        "Left and right are from the attacking team's view (low y is left).",
        "Zone edges are hard, so a small movement can change a zone. Zone shares say where the "
        "player received the ball, not where they were available and were not found.",
    ),
    MetricDefinition(
        "role_comparison",
        "Per-90 values and reception rates against peers: other players in the ingested matches "
        "whose minutes in the same role group reach the minimum. Only events and minutes in "
        "that role group count, for both the player and the peers. Rank 1 is the highest value.",
        "The default dataset holds five matches of one team and its opponents, so peer groups "
        "are small and unrepresentative of the tournament, and per-90 values from short "
        "appearances are volatile. Role groups come from StatsBomb's position labels, not "
        "from how a player actually played.",
    ),
)

METRICS: dict[str, MetricDefinition] = {item.name: item for item in _DEFINITIONS}


def render_definitions_markdown() -> str:
    """Render the registry as the body of docs/metrics.md."""
    lines = [
        "# Metric definitions",
        "",
        "Generated from `src/touchline/metrics/definitions.py` by "
        "`python -m touchline.metrics.docs`. Do not edit by hand.",
        "",
        "All locations use StatsBomb's coordinates: x from 0 to 120 along the pitch, y from 0 "
        "to 80 across it, with the acting team always attacking towards x = 120.",
    ]
    for item in _DEFINITIONS:
        lines += [
            "",
            f"## {item.name}",
            "",
            f"**Definition.** {item.definition}",
            "",
            f"**Limitations.** {item.limitations}",
        ]
    return "\n".join(lines) + "\n"


def render_definitions_prompt() -> str:
    """Render the registry for the agents' system prompt."""
    blocks = [
        f'<metric name="{item.name}">\nDefinition: {item.definition}\n'
        f"Limitations: {item.limitations}\n</metric>"
        for item in _DEFINITIONS
    ]
    return "\n".join(blocks)
