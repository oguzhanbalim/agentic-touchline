"""Minutes played and sendings-off, derived from the event stream.

The lineup files have a `positions` list that looks like the obvious source, but it is not
reliable. Its stints are ordered by clock rather than by period, so in extra-time matches a
change at 93:54 of period 2 sorts after one at 90:00 of period 3 and the stints scramble: one
England squad summed to 1,761 player-minutes for a match worth 1,467. The events are in a
reliable order, so this module walks those.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from touchline.schema import Event

# Clock time at which each period starts. Period 5 is a penalty shootout and is not open
# play, so it counts towards neither minutes nor the score.
PERIOD_START_SECONDS = {1: 0, 2: 45 * 60, 3: 90 * 60, 4: 105 * 60}

_SENDING_OFF_CARDS = frozenset({"Red Card", "Second Yellow"})


@dataclass(frozen=True)
class PlayerMinutes:
    team: str
    player_id: int
    position: str
    minutes: float


@dataclass(frozen=True)
class SendingOff:
    team: str
    player_id: int
    period: int
    minute: int
    second: int


def _period_ends(events: Sequence[Event]) -> dict[int, int]:
    ends: dict[int, int] = {}
    for event in events:
        if event.period in PERIOD_START_SECONDS:
            clock = event.minute * 60 + event.second
            ends[event.period] = max(ends.get(event.period, 0), clock)
    return ends


def _card_name(event: Event) -> str | None:
    holder = event.foul_committed or event.bad_behaviour
    return holder.card.name if holder is not None and holder.card is not None else None


def minutes_from_events(events: Sequence[Event]) -> tuple[list[PlayerMinutes], list[SendingOff]]:
    """Walk each team's changes in event order and credit time to whoever was on the pitch.

    Elapsed time is measured on a continuous timeline, so stoppage time never overlaps the
    clock of the next period. Time off the pitch for treatment counts as played: those
    "Player Off" events are not treated as changes.
    """
    ends = _period_ends(events)
    offsets: dict[int, int] = {}
    elapsed = 0
    for period in sorted(ends):
        offsets[period] = elapsed
        elapsed += ends[period] - PERIOD_START_SECONDS[period]

    def timeline(event: Event) -> int:
        clock = event.minute * 60 + event.second
        return offsets[event.period] + max(0, clock - PERIOD_START_SECONDS[event.period])

    seconds: dict[tuple[str, int, str], int] = defaultdict(int)
    sendings_off: list[SendingOff] = []
    on_pitch: dict[str, dict[int, str]] = defaultdict(dict)
    last_time: dict[str, int] = defaultdict(int)
    dismissed: dict[str, set[int]] = defaultdict(set)

    def credit(team: str, until: int) -> None:
        for player_id, position in on_pitch[team].items():
            seconds[(team, player_id, position)] += until - last_time[team]
        last_time[team] = until

    for event in sorted(events, key=lambda item: item.index):
        if event.period not in PERIOD_START_SECONDS:
            continue
        team = event.team.name
        if event.tactics is not None:
            credit(team, timeline(event))
            # A shift can still list someone already sent off, so that list has the last word.
            on_pitch[team] = {
                member.player.id: member.position.name
                for member in event.tactics.lineup
                if member.player.id not in dismissed[team]
            }
        elif event.substitution is not None and event.player is not None:
            credit(team, timeline(event))
            outgoing = on_pitch[team].pop(event.player.id, None)
            position = event.position.name if event.position is not None else outgoing
            if position is not None:
                on_pitch[team][event.substitution.replacement.id] = position
        elif _card_name(event) in _SENDING_OFF_CARDS and event.player is not None:
            # A card shown to someone on the bench changes nothing on the pitch.
            if event.player.id in on_pitch[team]:
                credit(team, timeline(event))
                del on_pitch[team][event.player.id]
                dismissed[team].add(event.player.id)
                sendings_off.append(
                    SendingOff(team, event.player.id, event.period, event.minute, event.second)
                )

    for team in list(on_pitch):
        credit(team, elapsed)
    rows = [
        PlayerMinutes(team, player_id, position, total / 60)
        for (team, player_id, position), total in seconds.items()
        if total > 0
    ]
    return rows, sendings_off
