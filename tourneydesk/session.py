"""SpecSession: a mutable draft of a TournamentSpec with per-fact provenance.

Pure and synchronous -- no LLM calls happen here. `tourneydesk.tools.dispatch`
is the only code that mutates a session, and it does so by calling the plain
methods on this class. Every method that records a tournament fact takes a
`source_quote` (the director's own words) so the fact can be traced back to
what was actually said, for the Rules panel.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from functools import wraps
from itertools import chain
from typing import Any, Concatenate, Literal

from tournament_scheduler.models import (
    CoachingConflict,
    ConstraintPriority,
    DivisionSpec,
    FieldPreference,
    FieldSize,
    FieldSpec,
    TeamAvoidance,
    TeamSpec,
    TimePreference,
    TimeWindow,
    TournamentSpec,
)
from tourneydesk.limits import SessionLimits

_OPTIONAL_DIVISION_FIELDS = (
    "halftime_minutes",
    "buffer_minutes",
    "min_rest_minutes",
    "games_per_team",
    "pool_size",
    "bracket_after_pools",
)

_DEFAULT_DESCRIPTIONS: dict[str, str] = {
    "halftime_minutes": "no halftime",
    "buffer_minutes": "a 10-minute changeover buffer",
    "min_rest_minutes": "60 minutes minimum rest between games",
    "games_per_team": "3 pool-play games per team",
    "pool_size": "pools of 4 teams",
    "bracket_after_pools": "an elimination bracket after pool play",
}


class IncompleteSpecError(Exception):
    """Raised by `SpecSession.to_spec()` when required facts are still missing.

    `missing` lists each missing REQUIRED fact in plain language, suitable for
    surfacing directly to a director or to the intake agent's clarifying
    question.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        message = "The tournament spec is not complete yet. Missing:\n" + "\n".join(f"  - {m}" for m in missing)
        super().__init__(message)


@dataclass
class DraftDivision:
    id: str
    name: str
    field_size: FieldSize
    game_duration_minutes: int
    game_format: str | None = None
    halftime_minutes: int | None = None
    buffer_minutes: int | None = None
    min_rest_minutes: int | None = None
    games_per_team: int | None = None
    pool_size: int | None = None
    bracket_after_pools: bool | None = None
    source_quotes: list[str] = field(default_factory=list)


@dataclass
class DraftTeam:
    id: str
    name: str
    division_id: str
    club: str | None = None
    seed: int | None = None
    source_quotes: list[str] = field(default_factory=list)


@dataclass
class DraftField:
    id: str
    name: str
    size: FieldSize
    availability: list[TimeWindow] = field(default_factory=list)
    source_quotes: list[str] = field(default_factory=list)


def _slugify(text: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "_" for c in text.strip())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "team"


def _division_defaults() -> dict[str, object]:
    return {name: DivisionSpec.model_fields[name].default for name in _OPTIONAL_DIVISION_FIELDS}


def _validated_division_spec(
    id: str,
    name: str,
    field_size: FieldSize,
    game_duration_minutes: int,
    **optional: Any,
) -> DivisionSpec:
    """Construct a DivisionSpec purely to validate the given values.

    Raises pydantic.ValidationError (or ValueError from the FieldSize cast at
    the call site) on anything out of range. None values are omitted so
    pydantic applies its own defaults.
    """
    kwargs: dict[str, Any] = {
        "id": id,
        "name": name,
        "field_size": field_size,
        "game_duration_minutes": game_duration_minutes,
    }
    for key, value in optional.items():
        if value is not None:
            kwargs[key] = value
    return DivisionSpec.model_validate(kwargs)


def _bounded_input[**P, R](
    method: Callable[Concatenate[SpecSession, P], R],
) -> Callable[Concatenate[SpecSession, P], R]:
    """Give direct mutations the same input envelope as tool dispatch."""

    @wraps(method)
    def checked(self: SpecSession, *args: P.args, **kwargs: P.kwargs) -> R:
        self.validate_tool_input(kwargs)
        return method(self, *args, **kwargs)

    return checked


class SpecSession:
    """Mutable draft of a TournamentSpec, with provenance per fact.

    ``limits=None`` preserves the unrestricted CLI/product behavior. Public
    callers can opt into ``SpecSession(limits=PUBLIC_DEMO_LIMITS)`` using the
    immutable envelope in :mod:`tourneydesk.limits`.
    """

    def __init__(self, *, limits: SessionLimits | None = None) -> None:
        self.limits = limits
        self.name: str = ""
        self.description: str = ""
        self._name_quotes: list[str] = []
        self.divisions: dict[str, DraftDivision] = {}
        self.teams: dict[str, DraftTeam] = {}
        self.fields: dict[str, DraftField] = {}
        self.coaching_conflicts: list[tuple[CoachingConflict, str]] = []
        self.team_avoidances: list[tuple[TeamAvoidance, str]] = []
        self.time_preferences: list[tuple[TimePreference, str]] = []
        self.field_preferences: list[tuple[FieldPreference, str]] = []
        self._assumptions: list[str] = []
        self.intake_complete: bool = False
        self._completion_quote: str | None = None
        # (spec fingerprint, SolveOutcome) — CP-SAT is nondeterministic across
        # runs, so every consumer (schedule panel, agent digest) must describe
        # the SAME solution for the same spec. core.service.solve_current owns it.
        self.solve_cache: tuple[str, Any] | None = None

    def validate_tool_input(self, values: dict[str, Any]) -> None:
        """Check raw tool arguments before parsing or mutating a bounded draft."""
        if self.limits is not None:
            self.limits.validate_input(values)

    def _all_provenance(self) -> Iterable[str]:
        yield from self._name_quotes
        for draft in chain(self.divisions.values(), self.teams.values(), self.fields.values()):
            yield from draft.source_quotes
        for _, quote in chain(
            self.coaching_conflicts, self.team_avoidances, self.time_preferences, self.field_preferences
        ):
            yield quote
        yield from self._assumptions
        if self._completion_quote is not None:
            yield self._completion_quote

    def _check_provenance(
        self,
        source_quote: str,
        *,
        copies: int = 1,
        removed: Iterable[str] = (),
        existing: Sequence[str] | None = None,
        assumption: str | None = None,
    ) -> None:
        if self.limits is None:
            return
        limits = self.limits
        if existing is not None:
            limits.check(len(existing) + 1, limits.max_quotes_per_fact, "quotes per fact")
        entries = 0
        characters = 0
        for quote in self._all_provenance():
            entries += 1
            characters += len(quote)
        for quote in removed:
            entries -= 1
            characters -= len(quote)
        entries += copies + (assumption is not None)
        characters += copies * len(source_quote) + (len(assumption) if assumption is not None else 0)
        limits.check(entries, limits.max_provenance_entries, "retained provenance entries")
        limits.check(characters, limits.max_provenance_chars, "retained provenance characters")

    def _check_team_capacity(self, division_id: str, count: int, *, replacing: bool = False) -> None:
        if self.limits is None:
            return
        current = sum(t.division_id == division_id for t in self.teams.values())
        division_total = count if replacing else current + count
        total = len(self.teams) - current + count if replacing else len(self.teams) + count
        self.limits.check(division_total, self.limits.max_teams_per_division, "teams per division")
        self.limits.check(total, self.limits.max_total_teams, "teams total")

    def _check_division_values(self, **values: Any) -> None:
        if self.limits is None:
            return
        for name, maximum in (
            ("game_duration_minutes", self.limits.max_game_duration_minutes),
            ("games_per_team", self.limits.max_games_per_team),
            ("min_rest_minutes", self.limits.max_min_rest_minutes),
            ("buffer_minutes", self.limits.max_buffer_minutes),
        ):
            value = values.get(name)
            if value is not None:
                self.limits.check(value, maximum, name)

    def _validated_windows(self, raw: list[dict[str, Any]], *, replacing_field: str | None = None) -> list[TimeWindow]:
        retained: list[TimeWindow] = []
        if self.limits is not None:
            self.limits.check(len(raw), self.limits.max_windows_per_fact, "windows per fact")
            retained = [
                window for field in self.fields.values() if field.id != replacing_field for window in field.availability
            ]
            retained.extend(window for pref, _ in self.time_preferences for window in pref.preferred_windows)
            self.limits.check(len(retained) + len(raw), self.limits.max_total_windows, "time windows total")
        windows = [TimeWindow.model_validate(w) for w in raw]
        if self.limits is not None and (retained or windows):
            days = self.limits.max_tournament_days
            if sum((w.end - w.start for w in windows), timedelta()) > timedelta(days=days):
                raise ValueError(f"Demo limit: combined windows per fact may not exceed {days} days of time.")
            all_windows = retained + windows
            try:
                earliest = min(w.start for w in all_windows)
                latest = max(w.end for w in all_windows)
            except TypeError as exc:
                raise ValueError("Demo input: time windows must use consistent time zones.") from exc
            # An end at midnight belongs to the previous playing day.
            first_day = min(w.start.date() for w in all_windows)
            last_day = max((w.end - timedelta(microseconds=1)).date() for w in all_windows)
            if latest - earliest > timedelta(days=days) or (last_day - first_day).days >= days:
                raise ValueError(f"Demo limit: time windows must fit within {days} consecutive days.")
        return windows

    def _check_relationship_capacity(self, *, replacing: int = 0) -> None:
        if self.limits is not None:
            count = sum(
                len(items)
                for items in (
                    self.coaching_conflicts,
                    self.team_avoidances,
                    self.time_preferences,
                    self.field_preferences,
                )
            )
            self.limits.check(count - replacing + 1, self.limits.max_relationships, "relationships and preferences")

    # -- Tournament info ----------------------------------------------------

    @_bounded_input
    def set_tournament_info(self, *, name: str, description: str | None, source_quote: str) -> None:
        self._check_provenance(source_quote, existing=self._name_quotes)
        self.name = name
        if description is not None:
            self.description = description
        self._name_quotes.append(source_quote)

    # -- Divisions ------------------------------------------------------------

    @_bounded_input
    def add_division(
        self,
        *,
        id: str,
        name: str,
        field_size: str,
        game_duration_minutes: int,
        game_format: str | None = None,
        halftime_minutes: int | None = None,
        buffer_minutes: int | None = None,
        min_rest_minutes: int | None = None,
        games_per_team: int | None = None,
        pool_size: int | None = None,
        bracket_after_pools: bool | None = None,
        source_quote: str,
    ) -> DraftDivision:
        previous = self.divisions.get(id)
        if self.limits is not None:
            self.limits.check(len(self.divisions) + (previous is None), self.limits.max_divisions, "divisions")
        self._check_division_values(
            game_duration_minutes=game_duration_minutes,
            games_per_team=games_per_team,
            min_rest_minutes=min_rest_minutes,
            buffer_minutes=buffer_minutes,
        )
        self._check_provenance(source_quote, removed=previous.source_quotes if previous is not None else ())
        size = FieldSize(field_size)
        _validated_division_spec(
            id,
            name,
            size,
            game_duration_minutes,
            halftime_minutes=halftime_minutes,
            buffer_minutes=buffer_minutes,
            min_rest_minutes=min_rest_minutes,
            games_per_team=games_per_team,
            pool_size=pool_size,
            bracket_after_pools=bracket_after_pools,
        )
        draft = DraftDivision(
            id=id,
            name=name,
            field_size=size,
            game_duration_minutes=game_duration_minutes,
            game_format=game_format,
            halftime_minutes=halftime_minutes,
            buffer_minutes=buffer_minutes,
            min_rest_minutes=min_rest_minutes,
            games_per_team=games_per_team,
            pool_size=pool_size,
            bracket_after_pools=bracket_after_pools,
        )
        draft.source_quotes.append(source_quote)
        self.divisions[id] = draft
        return draft

    @_bounded_input
    def update_division(
        self,
        *,
        id: str,
        name: str | None = None,
        field_size: str | None = None,
        game_duration_minutes: int | None = None,
        game_format: str | None = None,
        halftime_minutes: int | None = None,
        buffer_minutes: int | None = None,
        min_rest_minutes: int | None = None,
        games_per_team: int | None = None,
        pool_size: int | None = None,
        bracket_after_pools: bool | None = None,
        source_quote: str,
    ) -> DraftDivision:
        if id not in self.divisions:
            raise ValueError(f"Unknown division '{id}' -- add it first with add_division.")
        draft = self.divisions[id]
        new_name = name if name is not None else draft.name
        new_size = FieldSize(field_size) if field_size is not None else draft.field_size
        new_duration = game_duration_minutes if game_duration_minutes is not None else draft.game_duration_minutes
        merged: dict[str, Any] = {
            "halftime_minutes": halftime_minutes if halftime_minutes is not None else draft.halftime_minutes,
            "buffer_minutes": buffer_minutes if buffer_minutes is not None else draft.buffer_minutes,
            "min_rest_minutes": min_rest_minutes if min_rest_minutes is not None else draft.min_rest_minutes,
            "games_per_team": games_per_team if games_per_team is not None else draft.games_per_team,
            "pool_size": pool_size if pool_size is not None else draft.pool_size,
            "bracket_after_pools": bracket_after_pools
            if bracket_after_pools is not None
            else draft.bracket_after_pools,
        }
        self._check_division_values(game_duration_minutes=new_duration, **merged)
        self._check_provenance(source_quote, existing=draft.source_quotes)
        _validated_division_spec(id, new_name, new_size, new_duration, **merged)

        draft.name = new_name
        draft.field_size = new_size
        draft.game_duration_minutes = new_duration
        if game_format is not None:
            draft.game_format = game_format
        draft.halftime_minutes = merged["halftime_minutes"]
        draft.buffer_minutes = merged["buffer_minutes"]
        draft.min_rest_minutes = merged["min_rest_minutes"]
        draft.games_per_team = merged["games_per_team"]
        draft.pool_size = merged["pool_size"]
        draft.bracket_after_pools = merged["bracket_after_pools"]
        draft.source_quotes.append(source_quote)
        return draft

    @_bounded_input
    def remove_division(self, *, id: str, source_quote: str) -> bool:
        if id not in self.divisions:
            return False
        del self.divisions[id]
        for team_id in [tid for tid, t in self.teams.items() if t.division_id == id]:
            del self.teams[team_id]
        self._assumptions = [a for a in self._assumptions if id not in a]
        return True

    # -- Teams ------------------------------------------------------------------

    def _derive_team_id(self, division_id: str, name: str, reserved: set[str] | None = None) -> str:
        base = f"{division_id}_{_slugify(name)}"
        candidate = base
        suffix = 2
        while candidate in self.teams or (reserved is not None and candidate in reserved):
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    @_bounded_input
    def add_teams(self, *, division_id: str, teams: list[dict[str, Any]], source_quote: str) -> list[DraftTeam]:
        if division_id not in self.divisions:
            raise ValueError(f"Unknown division '{division_id}' -- add the division before adding its teams.")
        self._check_team_capacity(division_id, len(teams))
        self._check_provenance(source_quote, copies=len(teams))
        created: list[DraftTeam] = []
        reserved: set[str] = set()
        for entry in teams:
            name = str(entry["name"])
            # A duplicate name inside one division silently created a second team
            # that RENDERED as "Team 2 v Team 2" in the schedule (persona P4).
            existing = [
                t for t in chain(self.teams.values(), created) if t.division_id == division_id and t.name == name
            ]
            if existing:
                raise ValueError(
                    f"A team named '{name}' already exists in division '{division_id}' "
                    f"(id '{existing[0].id}'). To modify or reference the existing team, use its id; "
                    "teams are never duplicated by name."
                )
            raw_id = entry.get("id")
            team_id = str(raw_id) if raw_id else self._derive_team_id(division_id, name, reserved)
            if self.limits is not None:
                self.limits.check(len(team_id), self.limits.max_id_chars, "team id length")
                if team_id in self.teams or team_id in reserved:
                    raise ValueError(f"Team id '{team_id}' already exists; use a unique team id.")
            club = entry.get("club")
            seed = entry.get("seed")
            draft = DraftTeam(
                id=team_id,
                name=name,
                division_id=division_id,
                club=str(club) if club is not None else None,
                seed=int(seed) if seed is not None else None,
            )
            draft.source_quotes.append(source_quote)
            created.append(draft)
            reserved.add(team_id)
        # Validate the entire batch before publishing any roster changes.
        self.teams.update((draft.id, draft) for draft in created)
        return created

    @_bounded_input
    def set_team_count(self, *, division_id: str, count: int, source_quote: str) -> list[DraftTeam]:
        if division_id not in self.divisions:
            raise ValueError(f"Unknown division '{division_id}' -- add the division before setting a team count.")
        if self.limits is not None and type(count) is not int:
            raise ValueError("Demo input: team count must be an integer.")
        if count < 2:
            raise ValueError("Team count must be at least 2.")
        self._check_team_capacity(division_id, count, replacing=True)
        division_name = self.divisions[division_id].name
        assumption = f"Assumed placeholder team names for {division_name} ({count} teams, names not stated)"
        if self.limits is not None:
            self.limits.check(len(self._assumptions) + 1, self.limits.max_assumptions, "retained assumptions")
            for i in range(1, count + 1):
                team_id = f"{division_id}_team_{i:02d}"
                self.limits.check(len(team_id), self.limits.max_id_chars, "team id length")
                if team_id in self.teams and self.teams[team_id].division_id != division_id:
                    raise ValueError(f"Team id '{team_id}' already exists in another division.")
        self._check_provenance(
            source_quote,
            copies=count,
            removed=(
                quote for team in self.teams.values() if team.division_id == division_id for quote in team.source_quotes
            ),
            assumption=assumption,
        )
        for team_id in [tid for tid, t in self.teams.items() if t.division_id == division_id]:
            del self.teams[team_id]
        created: list[DraftTeam] = []
        for i in range(1, count + 1):
            team_id = f"{division_id}_team_{i:02d}"
            draft = DraftTeam(id=team_id, name=f"Team {i}", division_id=division_id)
            draft.source_quotes.append(source_quote)
            self.teams[team_id] = draft
            created.append(draft)
        self._assumptions.append(assumption)
        return created

    # -- Fields -------------------------------------------------------------

    @_bounded_input
    def add_field(
        self, *, id: str, name: str, size: str, availability: list[dict[str, Any]], source_quote: str
    ) -> DraftField:
        previous = self.fields.get(id)
        if self.limits is not None:
            self.limits.check(len(self.fields) + (previous is None), self.limits.max_fields, "fields")
        self._check_provenance(source_quote, removed=previous.source_quotes if previous is not None else ())
        field_size = FieldSize(size)
        windows = self._validated_windows(availability, replacing_field=id)
        draft = DraftField(id=id, name=name, size=field_size, availability=windows)
        draft.source_quotes.append(source_quote)
        self.fields[id] = draft
        return draft

    @_bounded_input
    def set_field_availability(
        self, *, field_id: str, availability: list[dict[str, Any]], source_quote: str
    ) -> DraftField:
        if field_id not in self.fields:
            raise ValueError(f"Unknown field '{field_id}' -- add the field first with add_field.")
        draft = self.fields[field_id]
        self._check_provenance(source_quote, existing=draft.source_quotes)
        windows = self._validated_windows(availability, replacing_field=field_id)
        draft.availability = windows
        draft.source_quotes.append(source_quote)
        return draft

    @_bounded_input
    def remove_field(self, *, id: str, source_quote: str) -> bool:
        if id not in self.fields:
            return False
        del self.fields[id]
        return True

    # -- Coaching conflicts ---------------------------------------------------

    @_bounded_input
    def add_coaching_conflict(self, *, coach_name: str, team_ids: list[str], source_quote: str) -> CoachingConflict:
        replaced = [q for c, q in self.coaching_conflicts if c.coach_name == coach_name]
        if self.limits is not None:
            self.limits.check(len(team_ids), self.limits.max_teams_per_coach, "teams per coach")
            self.limits.check(
                len(self.coaching_conflicts) - len(replaced) + 1,
                self.limits.max_coaching_conflicts,
                "coaching conflicts",
            )
        self._check_relationship_capacity(replacing=len(replaced))
        self._check_provenance(source_quote, removed=replaced)
        conflict = CoachingConflict(coach_name=coach_name, team_ids=list(team_ids))
        self.coaching_conflicts = [c for c in self.coaching_conflicts if c[0].coach_name != coach_name]
        self.coaching_conflicts.append((conflict, source_quote))
        return conflict

    @_bounded_input
    def remove_coaching_conflict(self, *, coach_name: str, source_quote: str) -> bool:
        before = len(self.coaching_conflicts)
        self.coaching_conflicts = [c for c in self.coaching_conflicts if c[0].coach_name != coach_name]
        return len(self.coaching_conflicts) < before

    # -- Team avoidances ------------------------------------------------------

    @_bounded_input
    def add_team_avoidance(self, *, team_ids: list[str], reason: str | None, source_quote: str) -> TeamAvoidance:
        key = frozenset(team_ids)
        replaced = [q for a, q in self.team_avoidances if frozenset(a.team_ids) == key]
        self._check_relationship_capacity(replacing=len(replaced))
        self._check_provenance(source_quote, removed=replaced)
        avoidance = TeamAvoidance(team_ids=list(team_ids), reason=reason or "")
        self.team_avoidances = [a for a in self.team_avoidances if frozenset(a[0].team_ids) != key]
        self.team_avoidances.append((avoidance, source_quote))
        return avoidance

    @_bounded_input
    def remove_team_avoidance(self, *, team_ids: list[str], source_quote: str) -> bool:
        key = frozenset(team_ids)
        before = len(self.team_avoidances)
        self.team_avoidances = [a for a in self.team_avoidances if frozenset(a[0].team_ids) != key]
        return len(self.team_avoidances) < before

    # -- Preferences ----------------------------------------------------------

    @_bounded_input
    def add_time_preference(
        self,
        *,
        target: str,
        target_type: Literal["team", "division"],
        windows: list[dict[str, Any]],
        priority: str | None,
        source_quote: str,
    ) -> TimePreference:
        self._check_relationship_capacity()
        self._check_provenance(source_quote)
        preferred_windows = self._validated_windows(windows)
        pref = TimePreference(
            target=target,
            target_type=target_type,
            preferred_windows=preferred_windows,
            priority=ConstraintPriority(priority) if priority else ConstraintPriority.MEDIUM,
        )
        self.time_preferences.append((pref, source_quote))
        return pref

    @_bounded_input
    def remove_time_preferences(self, *, target: str, source_quote: str) -> int:
        """Remove every time preference for `target`; returns how many were removed."""
        del source_quote  # provenance recorded by the caller's transcript
        before = len(self.time_preferences)
        self.time_preferences = [(p, q) for p, q in self.time_preferences if p.target != target]
        return before - len(self.time_preferences)

    @_bounded_input
    def remove_field_preferences(self, *, target: str, source_quote: str) -> int:
        """Remove every field preference for `target`; returns how many were removed."""
        del source_quote
        before = len(self.field_preferences)
        self.field_preferences = [(p, q) for p, q in self.field_preferences if p.target != target]
        return before - len(self.field_preferences)

    @_bounded_input
    def add_field_preference(
        self,
        *,
        target: str,
        target_type: Literal["team", "division"],
        field_ids: list[str],
        priority: str | None,
        source_quote: str,
    ) -> FieldPreference:
        if self.limits is not None:
            self.limits.check(len(field_ids), self.limits.max_fields, "fields per preference")
        self._check_relationship_capacity()
        self._check_provenance(source_quote)
        pref = FieldPreference(
            target=target,
            target_type=target_type,
            preferred_field_ids=list(field_ids),
            priority=ConstraintPriority(priority) if priority else ConstraintPriority.LOW,
        )
        self.field_preferences.append((pref, source_quote))
        return pref

    # -- Completion -------------------------------------------------------------

    @_bounded_input
    def mark_intake_complete(self, *, confirmation_quote: str) -> None:
        self._check_provenance(
            confirmation_quote, removed=() if self._completion_quote is None else (self._completion_quote,)
        )
        self.intake_complete = True
        self._completion_quote = confirmation_quote

    # -- Materialization --------------------------------------------------------

    def to_spec(self) -> tuple[TournamentSpec, list[str]]:
        """Materialize a valid TournamentSpec, or raise IncompleteSpecError.

        Returns the spec plus a list of labeled-assumption strings for any
        OPTIONAL field that was left unstated and filled with a default.
        Missing REQUIRED facts (no divisions, <2 teams, no fields, or a field
        with no availability window) raise IncompleteSpecError rather than
        being silently defaulted.
        """
        missing: list[str] = []
        if not self.divisions:
            missing.append("At least one division (age group / bracket) has not been stated yet.")
        if len(self.teams) < 2:
            missing.append("Fewer than two teams have been stated. Need at least two teams total.")
        if not self.fields:
            missing.append("At least one field has not been stated yet.")
        for f in self.fields.values():
            if not f.availability:
                missing.append(f"No availability window has been stated for field '{f.name}' ({f.id}).")
        if missing:
            raise IncompleteSpecError(missing)

        assumptions: list[str] = list(self._assumptions)

        defaults = _division_defaults()
        division_specs: list[DivisionSpec] = []
        for d in self.divisions.values():
            kwargs: dict[str, Any] = {
                "id": d.id,
                "name": d.name,
                "field_size": d.field_size,
                "game_format": d.game_format,
                "game_duration_minutes": d.game_duration_minutes,
            }
            for name, default in defaults.items():
                value = getattr(d, name)
                if value is None:
                    kwargs[name] = default
                    assumptions.append(f"Assumed {_DEFAULT_DESCRIPTIONS[name]} for {d.name} (not stated)")
                else:
                    kwargs[name] = value
            division_specs.append(DivisionSpec.model_validate(kwargs))

        team_specs = [
            TeamSpec(id=t.id, name=t.name, division_id=t.division_id, club=t.club, seed=t.seed)
            for t in self.teams.values()
        ]
        field_specs = [
            FieldSpec(id=f.id, name=f.name, size=f.size, availability=list(f.availability))
            for f in self.fields.values()
        ]

        if not self.name:
            assumptions.append("Assumed tournament name 'Untitled Tournament' (not stated)")

        spec = TournamentSpec(
            name=self.name or "Untitled Tournament",
            description=self.description,
            divisions=division_specs,
            teams=team_specs,
            fields=field_specs,
            coaching_conflicts=[c for c, _ in self.coaching_conflicts],
            team_avoidances=[a for a, _ in self.team_avoidances],
            time_preferences=[p for p, _ in self.time_preferences],
            field_preferences=[p for p, _ in self.field_preferences],
        )
        return spec, assumptions

    def to_rules_json(self) -> dict[str, object]:
        """Serialize the draft as constraint cards grouped by category.

        Each card carries its NL origin quote(s), for the Rules panel.
        """
        return {
            "tournament": {
                "name": self.name,
                "description": self.description,
                "source_quotes": list(self._name_quotes),
            },
            "divisions": [
                {
                    "id": d.id,
                    "name": d.name,
                    "field_size": d.field_size.value,
                    "game_format": d.game_format,
                    "game_duration_minutes": d.game_duration_minutes,
                    "halftime_minutes": d.halftime_minutes,
                    "buffer_minutes": d.buffer_minutes,
                    "min_rest_minutes": d.min_rest_minutes,
                    "games_per_team": d.games_per_team,
                    "pool_size": d.pool_size,
                    "bracket_after_pools": d.bracket_after_pools,
                    "source_quotes": list(d.source_quotes),
                }
                for d in self.divisions.values()
            ],
            "teams": [
                {
                    "id": t.id,
                    "name": t.name,
                    "division_id": t.division_id,
                    "club": t.club,
                    "seed": t.seed,
                    "source_quotes": list(t.source_quotes),
                }
                for t in self.teams.values()
            ],
            "fields": [
                {
                    "id": f.id,
                    "name": f.name,
                    "size": f.size.value,
                    "availability": [{"start": w.start.isoformat(), "end": w.end.isoformat()} for w in f.availability],
                    "source_quotes": list(f.source_quotes),
                }
                for f in self.fields.values()
            ],
            "coaching_conflicts": [
                {"coach_name": c.coach_name, "team_ids": list(c.team_ids), "source_quotes": [q]}
                for c, q in self.coaching_conflicts
            ],
            "team_avoidances": [
                {"team_ids": list(a.team_ids), "reason": a.reason, "source_quotes": [q]}
                for a, q in self.team_avoidances
            ],
            "time_preferences": [
                {
                    "target": p.target,
                    "target_type": p.target_type,
                    "preferred_windows": [
                        {"start": w.start.isoformat(), "end": w.end.isoformat()} for w in p.preferred_windows
                    ],
                    "priority": p.priority.value,
                    "source_quotes": [q],
                }
                for p, q in self.time_preferences
            ],
            "field_preferences": [
                {
                    "target": p.target,
                    "target_type": p.target_type,
                    "preferred_field_ids": list(p.preferred_field_ids),
                    "priority": p.priority.value,
                    "source_quotes": [q],
                }
                for p, q in self.field_preferences
            ],
            "intake_complete": self.intake_complete,
        }
