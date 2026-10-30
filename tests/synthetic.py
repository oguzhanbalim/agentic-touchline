"""An invented match in StatsBomb's JSON shape.

The StatsBomb agreement forbids redistributing its data, so offline tests run on this
hand-built match. Every player, team, event and coordinate here is made up, and each
value is chosen so a test can state the expected metric by hand.
"""

from __future__ import annotations

import itertools
import json
import uuid
from typing import Any

COMPETITION_ID = 1
SEASON_ID = 1
MATCH_ID = 1001
ALPHA = "Synthetic Alpha"
BETA = "Synthetic Beta"

KEEPER_A, DEFENDER_A, MID_A, FORWARD_A, SUB_A = 10, 11, 12, 13, 14
KEEPER_B, DEFENDER_B, MID_B, FORWARD_B = 20, 21, 22, 23

_NAMES = {
    KEEPER_A: "Alpha Keeper",
    DEFENDER_A: "Alpha Defender",
    MID_A: "Alpha Midfielder",
    FORWARD_A: "Alpha Şükrü Forward",
    SUB_A: "Alpha Substitute",
    KEEPER_B: "Beta Keeper",
    DEFENDER_B: "Beta Defender",
    MID_B: "Beta Midfielder",
    FORWARD_B: "Beta Forward",
}
_TEAM_IDS = {ALPHA: 1, BETA: 2}
_POSITIONS = {
    "Goalkeeper": 1,
    "Center Back": 4,
    "Center Midfield": 14,
    "Center Forward": 23,
}

_ids = itertools.count(1)


def _ref(name: str, id_: int = 1) -> dict[str, Any]:
    return {"id": id_, "name": name}


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"touchline-synthetic/{label}"))


def _event(
    label: str,
    type_: str,
    team: str,
    player: int | None = None,
    position: str | None = None,
    location: list[float] | None = None,
    minute: int = 10,
    second: int = 0,
    period: int = 1,
    **extra: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": _uuid(label),
        "index": next(_ids),
        "period": period,
        "timestamp": f"00:{minute:02d}:{second:02d}.000",
        "minute": minute,
        "second": second,
        "type": _ref(type_),
        "possession": 1,
        "possession_team": _ref(team, _TEAM_IDS[team]),
        "play_pattern": _ref("Regular Play"),
        "team": _ref(team, _TEAM_IDS[team]),
        **extra,
    }
    if player is not None:
        event["player"] = _ref(_NAMES[player], player)
    if position is not None:
        event["position"] = _ref(position, _POSITIONS[position])
    if location is not None:
        event["location"] = location
    return event


def event(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """The event builder, for tests that assemble their own short match."""
    return _event(*args, **kwargs)


def tactics(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _tactics(*args, **kwargs)


def _pass(
    label: str,
    player: int,
    position: str,
    start: list[float],
    end: list[float],
    recipient: int | None,
    *,
    team: str = ALPHA,
    outcome: str | None = None,
    pass_type: str | None = None,
    minute: int = 10,
    period: int = 1,
    **pass_extra: Any,
) -> dict[str, Any]:
    detail: dict[str, Any] = {"length": 1.0, "end_location": end, **pass_extra}
    if recipient is not None:
        detail["recipient"] = _ref(_NAMES[recipient], recipient)
    if outcome is not None:
        detail["outcome"] = _ref(outcome)
    if pass_type is not None:
        detail["type"] = _ref(pass_type)
    return _event(
        label,
        "Pass",
        team,
        player,
        position,
        start,
        minute=minute,
        period=period,
        **{"pass": detail},
    )


def _shot(
    label: str, player: int, position: str, team: str, xg: float, outcome: str,
    minute: int = 20, period: int = 1,
) -> dict[str, Any]:  # fmt: skip
    return _event(
        label, "Shot", team, player, position, [108.0, 40.0], minute=minute, period=period,
        shot={
            "statsbomb_xg": xg,
            "end_location": [120.0, 40.0],
            "outcome": _ref(outcome),
            "type": _ref("Open Play"),
            "body_part": _ref("Left Foot"),
        },
    )  # fmt: skip


def _receipt(
    label: str, player: int, position: str, location: list[float], related: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    extra: dict[str, Any] = {}
    if related:
        extra["related_events"] = [_uuid(related)]
    if outcome:
        extra["ball_receipt"] = {"outcome": _ref(outcome)}
    return _event(label, "Ball Receipt*", ALPHA, player, position, location, **extra)


def _tactics(
    label: str,
    type_: str,
    team: str,
    members: list[tuple[int, str]],
    minute: int,
    period: int = 1,
) -> dict[str, Any]:
    lineup = [
        {"player": _ref(_NAMES[player], player), "position": _ref(position, _POSITIONS[position])}
        for player, position in members
    ]
    return _event(label, type_, team, minute=minute, period=period, tactics={"lineup": lineup})


_ALPHA_XI = [
    (KEEPER_A, "Goalkeeper"),
    (DEFENDER_A, "Center Back"),
    (MID_A, "Center Midfield"),
    (FORWARD_A, "Center Forward"),
]
_ALPHA_SHIFTED = [
    (KEEPER_A, "Goalkeeper"),
    (DEFENDER_A, "Center Back"),
    (MID_A, "Center Forward"),
    (FORWARD_A, "Center Forward"),
]
_ALPHA_AFTER_SUB = [
    (KEEPER_A, "Goalkeeper"),
    (DEFENDER_A, "Center Back"),
    (SUB_A, "Center Midfield"),
    (FORWARD_A, "Center Forward"),
]
_BETA_XI = [
    (KEEPER_B, "Goalkeeper"),
    (DEFENDER_B, "Center Back"),
    (MID_B, "Center Midfield"),
    (FORWARD_B, "Center Forward"),
]


def _carry(label: str, start: list[float], end: list[float]) -> dict[str, Any]:
    return _event(
        label, "Carry", ALPHA, MID_A, "Center Midfield", start, carry={"end_location": end}
    )


def build_events() -> list[dict[str, Any]]:
    cm, cf, cb, gk = "Center Midfield", "Center Forward", "Center Back", "Goalkeeper"
    return [
        _tactics("xi-a", "Starting XI", ALPHA, _ALPHA_XI, minute=0),
        _tactics("xi-b", "Starting XI", BETA, _BETA_XI, minute=0),
        # Progressive-pass cases for the Alpha midfielder. Distances are to (120, 40).
        _pass("prog-clear", MID_A, cm, [60, 40], [100, 40], FORWARD_A),  # 60 to 20
        _pass("prog-boundary", MID_A, cm, [80, 40], [90, 40], FORWARD_A),  # exactly 25%, 10 yards
        _pass("not-prog-small-share", MID_A, cm, [60, 40], [65, 40], FORWARD_A),  # 8%
        _pass("not-prog-near-goal", MID_A, cm, [112, 40], [115, 40], FORWARD_A),  # 37%, 3 yards
        _pass("not-prog-incomplete", MID_A, cm, [60, 40], [100, 40], None, outcome="Incomplete"),
        _pass("not-prog-corner", MID_A, cm, [119, 1], [110, 40], FORWARD_A, pass_type="Corner"),
        _pass("recovery-open-play", MID_A, cm, [60, 40], [62, 40], FORWARD_A, pass_type="Recovery"),
        _pass("gk-to-def", KEEPER_A, gk, [6, 40], [30, 40], DEFENDER_A),
        _pass("def-to-mid", DEFENDER_A, cb, [30, 40], [50, 40], MID_A),
        # Carries.
        _event("carry-prog", "Carry", ALPHA, MID_A, cm, [40, 40], carry={"end_location": [70, 40]}),
        _event(
            "carry-short", "Carry", ALPHA, MID_A, cm, [40, 40], carry={"end_location": [41, 40]}
        ),
        # Goals: Alpha one shot and one own goal for; Beta one shot. A shootout goal must not count.
        _shot("alpha-goal", FORWARD_A, cf, ALPHA, 0.30, "Goal"),
        _shot("alpha-saved", FORWARD_A, cf, ALPHA, 0.10, "Saved", minute=30),
        _shot("beta-goal", FORWARD_B, cf, BETA, 0.50, "Goal", minute=35),
        _event("og-against", "Own Goal Against", BETA, DEFENDER_B, cb, [5, 40], minute=40),
        _event("og-for", "Own Goal For", ALPHA, FORWARD_A, cf, [115, 40], minute=40),
        _shot("alpha-shootout", FORWARD_A, cf, ALPHA, 0.76, "Goal", minute=121, period=5),
        # Pressures by the Beta midfielder, one in each third of Beta's own attacking direction.
        _event("press-def", "Pressure", BETA, MID_B, cm, [20, 40]),
        _event("press-mid", "Pressure", BETA, MID_B, cm, [50, 40]),
        _event("press-att", "Pressure", BETA, MID_B, cm, [90, 40]),
        # Receptions for the Alpha forward; frames are in build_frames.
        _receipt("rec-between", FORWARD_A, cf, [90, 40], related="prog-clear"),
        _receipt("rec-censored", FORWARD_A, cf, [90, 40]),
        _receipt("rec-no-frame", FORWARD_A, cf, [90, 40]),
        _receipt("rec-misaligned", FORWARD_A, cf, [90, 40]),
        _receipt("rec-failed", FORWARD_A, cf, [90, 40], outcome="Incomplete"),
        _receipt("rec-corner", FORWARD_A, cf, [90, 40], related="not-prog-corner"),
        # Period 1 ends at 46:00. Bringing the midfielder forward at half time means period 2
        # kicks off with a clock of 45:00, which must add nothing to period 1's stoppage time.
        _event("half-1-end", "Half End", ALPHA, minute=46, period=1),
        _event("half-1-end-b", "Half End", BETA, minute=46, period=1),
        _tactics("shift", "Tactical Shift", ALPHA, _ALPHA_SHIFTED, minute=45, period=2),
        # Off at 60:00 for the substitute, who takes the midfield role straight away.
        _event(
            "sub",
            "Substitution",
            ALPHA,
            MID_A,
            cf,
            minute=60,
            period=2,
            substitution={"replacement": _ref(_NAMES[SUB_A], SUB_A), "outcome": _ref("Tactical")},
        ),
        _tactics("shift-2", "Tactical Shift", ALPHA, _ALPHA_AFTER_SUB, minute=60, period=2),
        # Second yellow at 70:00.
        _event(
            "second-yellow",
            "Foul Committed",
            BETA,
            DEFENDER_B,
            cb,
            [30, 40],
            minute=70,
            period=2,
            foul_committed={"card": _ref("Second Yellow")},
        ),
        # After Alpha's substitution, so outside the pass-network window.
        _pass("after-sub", FORWARD_A, cf, [90, 40], [92, 40], SUB_A, minute=70, period=2),
        # A card for a player not on the pitch must change nothing.
        _event(
            "bench-red",
            "Bad Behaviour",
            ALPHA,
            MID_A,
            minute=91,
            period=2,
            bad_behaviour={"card": _ref("Red Card")},
        ),
        _event("half-2-end", "Half End", ALPHA, minute=92, period=2),
        _event("half-2-end-b", "Half End", BETA, minute=92, period=2),
    ]


_WHOLE_PITCH = [0.0, 0.0, 120.0, 0.0, 120.0, 80.0, 0.0, 80.0, 0.0, 0.0]
_TINY_AREA = [85.0, 35.0, 92.0, 35.0, 92.0, 45.0, 85.0, 45.0, 85.0, 35.0]


def _player(
    x: float, y: float, *, teammate: bool, actor: bool = False, keeper: bool = False
) -> dict:
    return {"teammate": teammate, "actor": actor, "keeper": keeper, "location": [x, y]}


def _ring_opponents() -> list[dict[str, Any]]:
    """Eight outfield opponents with two goal-side and two ball-side of (90, 40)."""
    return [
        _player(x, y, teammate=False)
        for x, y in (
            (95, 40),  # nearest, 5 yards goal-side
            (100, 45),
            (85, 32),
            (82, 50),
            (70, 10),
            (70, 70),
            (112, 20),
            (112, 60),
        )
    ]


def build_frames() -> list[dict[str, Any]]:
    actor = _player(90, 40, teammate=True, actor=True)
    return [
        {
            "event_uuid": _uuid("rec-between"),
            "visible_area": _WHOLE_PITCH,
            "freeze_frame": [actor, *_ring_opponents()],
        },
        {
            "event_uuid": _uuid("rec-censored"),
            "visible_area": _TINY_AREA,
            "freeze_frame": [actor, *_ring_opponents()],
        },
        {
            "event_uuid": _uuid("rec-misaligned"),
            "visible_area": _WHOLE_PITCH,
            "freeze_frame": [_player(30, 40, teammate=True, actor=True), *_ring_opponents()],
        },
        {
            "event_uuid": _uuid("rec-failed"),
            "visible_area": _WHOLE_PITCH,
            "freeze_frame": [actor, *_ring_opponents()],
        },
    ]


def build_lineups() -> list[dict[str, Any]]:
    def entry(player: int, number: int) -> dict[str, Any]:
        return {
            "player_id": player,
            "player_name": _NAMES[player],
            "player_nickname": None,
            "jersey_number": number,
            "country": _ref("Nowhere"),
            "cards": [],
            "positions": [],
        }

    return [
        {
            "team_id": 1,
            "team_name": ALPHA,
            "lineup": [
                entry(KEEPER_A, 1),
                entry(DEFENDER_A, 4),
                entry(MID_A, 8),
                entry(SUB_A, 12),
                entry(FORWARD_A, 9),
            ],
        },
        {
            "team_id": 2,
            "team_name": BETA,
            "lineup": [
                entry(KEEPER_B, 1),
                entry(DEFENDER_B, 4),
                entry(MID_B, 8),
                entry(FORWARD_B, 9),
            ],
        },
    ]


def build_matches() -> list[dict[str, Any]]:
    return [
        {
            "match_id": MATCH_ID,
            "match_date": "2024-01-15",
            "competition": {"competition_id": COMPETITION_ID, "competition_name": "Synthetic Cup"},
            "season": {"season_id": SEASON_ID, "season_name": "2024"},
            "home_team": {"home_team_name": ALPHA},
            "away_team": {"away_team_name": BETA},
            "home_score": 2,
            "away_score": 1,
            "competition_stage": {"name": "Final"},
        }
    ]


def build_files() -> dict[str, bytes]:
    """Map open-data paths to file bytes, as the real repository would serve them."""
    base = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
    return {
        f"{base}/matches/{COMPETITION_ID}/{SEASON_ID}.json": json.dumps(build_matches()).encode(),
        f"{base}/events/{MATCH_ID}.json": json.dumps(build_events()).encode(),
        f"{base}/lineups/{MATCH_ID}.json": json.dumps(build_lineups()).encode(),
        f"{base}/three-sixty/{MATCH_ID}.json": json.dumps(build_frames()).encode(),
    }
