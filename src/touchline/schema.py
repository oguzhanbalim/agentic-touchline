"""Typed views of the StatsBomb JSON this project reads.

Only the fields the metrics use are modelled. Unknown fields are ignored, but a
missing or wrongly typed field that is modelled fails loudly at ingest rather than
turning into a wrong number later.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


Location = Annotated[list[float], Field(min_length=2, max_length=3)]


class Ref(_Model):
    id: int
    name: str


class PassDetail(_Model):
    recipient: Ref | None = None
    length: float
    end_location: Location
    height: Ref | None = None
    type: Ref | None = None
    outcome: Ref | None = None
    cross: bool = False
    through_ball: bool = False
    shot_assist: bool = False
    goal_assist: bool = False


class CarryDetail(_Model):
    end_location: Location


class ShotDetail(_Model):
    statsbomb_xg: float
    end_location: Location
    outcome: Ref
    type: Ref
    body_part: Ref | None = None


class BallReceiptDetail(_Model):
    outcome: Ref | None = None


class Event(_Model):
    id: str
    index: int
    period: int
    minute: int
    second: int
    type: Ref
    possession: int
    possession_team: Ref
    play_pattern: Ref
    team: Ref
    player: Ref | None = None
    position: Ref | None = None
    location: Location | None = None
    under_pressure: bool = False
    related_events: list[str] = Field(default_factory=list)
    pass_: PassDetail | None = Field(default=None, alias="pass")
    carry: CarryDetail | None = None
    shot: ShotDetail | None = None
    ball_receipt: BallReceiptDetail | None = None


class FramePlayer(_Model):
    teammate: bool
    actor: bool
    keeper: bool
    location: Location


class Frame(_Model):
    event_uuid: str
    visible_area: list[float] = Field(default_factory=list)
    freeze_frame: list[FramePlayer] = Field(default_factory=list)


class Stint(_Model):
    """A spell in one position, in game-clock time. `to` is null at the final whistle."""

    position: str
    from_: str = Field(alias="from")
    to: str | None = None
    from_period: int
    to_period: int | None = None


class Card(_Model):
    time: str
    card_type: str
    period: int


class LineupPlayer(_Model):
    player_id: int
    player_name: str
    jersey_number: int | None = None
    cards: list[Card] = Field(default_factory=list)
    positions: list[Stint] = Field(default_factory=list)


class TeamLineup(_Model):
    team_id: int
    team_name: str
    lineup: list[LineupPlayer]


class _NamedCompetition(_Model):
    competition_id: int
    competition_name: str


class _NamedSeason(_Model):
    season_id: int
    season_name: str


class _HomeTeam(_Model):
    home_team_name: str


class _AwayTeam(_Model):
    away_team_name: str


class _Stage(_Model):
    name: str


class Match(_Model):
    match_id: int
    match_date: str
    competition: _NamedCompetition
    season: _NamedSeason
    home_team: _HomeTeam
    away_team: _AwayTeam
    home_score: int
    away_score: int
    competition_stage: _Stage

    @property
    def teams(self) -> tuple[str, str]:
        return self.home_team.home_team_name, self.away_team.away_team_name
