"""Domain models for tournament scheduling.

Defines the declarative specification that describes a tournament:
teams, fields, time windows, divisions, constraints, and format.
Designed as a clean target for an LLM intake layer to populate.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FieldSize(Enum):
    """Field size categories matching youth soccer standards."""

    SMALL = "small"  # 4v4 / 3v3 (U8 and younger)
    MEDIUM = "medium"  # 7v7 (U9-U10)
    LARGE = "large"  # 9v9 (U11-U12)
    FULL = "full"  # 11v11 (U13+)


class ConstraintPriority(Enum):
    """Priority levels for soft constraints."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Core domain objects
# ---------------------------------------------------------------------------


class TimeWindow(BaseModel):
    """A window of time when a field is available."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_window(self) -> TimeWindow:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class FieldSpec(BaseModel):
    """A playing field with availability and size constraints."""

    id: str
    name: str
    size: FieldSize
    availability: list[TimeWindow] = Field(min_length=1)


class TeamSpec(BaseModel):
    """A team participating in the tournament."""

    id: str
    name: str
    division_id: str
    club: str | None = None
    seed: int | None = None  # 1 = highest seed


class DivisionSpec(BaseModel):
    """A division / age group (e.g. U12 Boys Gold).

    Teams within a division compete together in pool play and brackets.
    """

    id: str
    name: str
    field_size: FieldSize
    game_duration_minutes: int = Field(ge=10, le=120)
    halftime_minutes: int = Field(default=0, ge=0, le=15)
    buffer_minutes: int = Field(default=10, ge=0, le=60, description="Changeover buffer between games on same field")
    min_rest_minutes: int = Field(default=60, ge=0, description="Minimum rest between games for any team")
    games_per_team: int = Field(default=3, ge=1, le=10, description="Number of pool-play games per team")
    pool_size: int = Field(default=4, ge=3, le=8, description="Target number of teams per pool")
    bracket_after_pools: bool = Field(default=True, description="Whether to play an elimination bracket after pools")


class CoachingConflict(BaseModel):
    """A coach who coaches multiple teams and cannot be in two places at once."""

    coach_name: str
    team_ids: list[str] = Field(min_length=2)


class TeamAvoidance(BaseModel):
    """Two teams that should not play at the same time (e.g. same fan base, siblings)."""

    team_ids: list[str] = Field(min_length=2, max_length=2)
    reason: str = ""


class TimePreference(BaseModel):
    """A team or division's preference for playing in a certain time window."""

    target: str  # team_id or division_id
    target_type: Literal["team", "division"] = "division"
    preferred_windows: list[TimeWindow]
    priority: ConstraintPriority = ConstraintPriority.MEDIUM


class FieldPreference(BaseModel):
    """A team or division's preference for playing on specific fields."""

    target: str  # team_id or division_id
    target_type: Literal["team", "division"] = "division"
    preferred_field_ids: list[str]
    priority: ConstraintPriority = ConstraintPriority.LOW


# ---------------------------------------------------------------------------
# Soft constraint weights (mapped from ConstraintPriority)
# ---------------------------------------------------------------------------

PRIORITY_WEIGHTS: dict[ConstraintPriority, int] = {
    ConstraintPriority.LOW: 1,
    ConstraintPriority.MEDIUM: 3,
    ConstraintPriority.HIGH: 7,
    ConstraintPriority.CRITICAL: 15,
}


# ---------------------------------------------------------------------------
# Top-level tournament specification
# ---------------------------------------------------------------------------


class TournamentSpec(BaseModel):
    """Complete declarative specification for a tournament.

    This is the single input document that describes everything the solver
    needs to produce a schedule. Designed to be the target schema for an
    LLM intake layer (natural language -> TournamentSpec).
    """

    name: str
    description: str = ""

    # Core entities
    divisions: list[DivisionSpec] = Field(min_length=1)
    teams: list[TeamSpec] = Field(min_length=2)
    fields: list[FieldSpec] = Field(min_length=1)

    # Constraint parameters
    coaching_conflicts: list[CoachingConflict] = Field(default_factory=list)
    team_avoidances: list[TeamAvoidance] = Field(default_factory=list)
    time_preferences: list[TimePreference] = Field(default_factory=list)
    field_preferences: list[FieldPreference] = Field(default_factory=list)

    # Solver configuration
    max_solve_seconds: int = Field(default=60, ge=1, le=3600)
    num_workers: int = Field(default=8, ge=1, le=32)

    @field_validator("teams")
    @classmethod
    def validate_unique_team_ids(cls, teams: list[TeamSpec]) -> list[TeamSpec]:
        ids = [t.id for t in teams]
        if len(ids) != len(set(ids)):
            raise ValueError("Team IDs must be unique")
        return teams

    @field_validator("fields")
    @classmethod
    def validate_unique_field_ids(cls, fields: list[FieldSpec]) -> list[FieldSpec]:
        ids = [f.id for f in fields]
        if len(ids) != len(set(ids)):
            raise ValueError("Field IDs must be unique")
        return fields

    @field_validator("divisions")
    @classmethod
    def validate_unique_division_ids(cls, divisions: list[DivisionSpec]) -> list[DivisionSpec]:
        ids = [d.id for d in divisions]
        if len(ids) != len(set(ids)):
            raise ValueError("Division IDs must be unique")
        return divisions

    @model_validator(mode="after")
    def validate_references(self) -> TournamentSpec:
        """Ensure all team division_ids reference existing divisions."""
        div_ids = {d.id for d in self.divisions}
        field_ids = {f.id for f in self.fields}
        team_ids = {t.id for t in self.teams}

        for team in self.teams:
            if team.division_id not in div_ids:
                raise ValueError(f"Team '{team.id}' references unknown division '{team.division_id}'")

        for conflict in self.coaching_conflicts:
            for tid in conflict.team_ids:
                if tid not in team_ids:
                    raise ValueError(f"Coaching conflict references unknown team '{tid}'")

        for avoidance in self.team_avoidances:
            for tid in avoidance.team_ids:
                if tid not in team_ids:
                    raise ValueError(f"Team avoidance references unknown team '{tid}'")

        for pref in self.field_preferences:
            for fid in pref.preferred_field_ids:
                if fid not in field_ids:
                    raise ValueError(f"Field preference references unknown field '{fid}'")

        return self

    def teams_in_division(self, division_id: str) -> list[TeamSpec]:
        """Return all teams in a given division."""
        return [t for t in self.teams if t.division_id == division_id]

    def fields_for_size(self, size: FieldSize) -> list[FieldSpec]:
        """Return fields that match a given size."""
        return [f for f in self.fields if f.size == size]

    def total_game_minutes(self, division: DivisionSpec) -> int:
        """Total minutes a single game occupies on a field (including buffer)."""
        return division.game_duration_minutes + division.halftime_minutes + division.buffer_minutes


# ---------------------------------------------------------------------------
# Solution models
# ---------------------------------------------------------------------------


class ScheduledGame(BaseModel):
    """A single scheduled game in the solution."""

    game_id: str
    division_id: str
    pool_id: str
    home_team_id: str
    away_team_id: str
    field_id: str
    start_time: datetime
    end_time: datetime
    game_number: int = 0  # Pool-play game number for this matchup

    @property
    def duration(self) -> timedelta:
        return self.end_time - self.start_time


class Pool(BaseModel):
    """A pool of teams within a division for round-robin play."""

    pool_id: str
    division_id: str
    team_ids: list[str]


class SolveStats(BaseModel):
    """Statistics from the solver run."""

    status: str  # OPTIMAL, FEASIBLE, INFEASIBLE, etc.
    wall_time_seconds: float
    objective_value: float | None = None
    num_conflicts: int = 0
    num_branches: int = 0
    num_games_scheduled: int = 0
    num_teams: int = 0
    num_fields: int = 0
    num_divisions: int = 0


class TournamentSchedule(BaseModel):
    """Complete solution: pools + scheduled games + solver stats."""

    tournament_name: str
    pools: list[Pool]
    games: list[ScheduledGame]
    stats: SolveStats

    def games_for_team(self, team_id: str) -> list[ScheduledGame]:
        """Return all games for a team, sorted by start time."""
        return sorted(
            [g for g in self.games if g.home_team_id == team_id or g.away_team_id == team_id],
            key=lambda g: g.start_time,
        )

    def games_on_field(self, field_id: str) -> list[ScheduledGame]:
        """Return all games on a field, sorted by start time."""
        return sorted(
            [g for g in self.games if g.field_id == field_id],
            key=lambda g: g.start_time,
        )

    def games_in_division(self, division_id: str) -> list[ScheduledGame]:
        """Return all games in a division, sorted by start time."""
        return sorted(
            [g for g in self.games if g.division_id == division_id],
            key=lambda g: g.start_time,
        )
