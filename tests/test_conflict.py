"""Golden-set tests for the infeasibility engine (conflict extraction).

Each infeasible spec below is *tiny* and deterministically infeasible, with a
known true conflict. We assert that `extract_conflict` returns a `ConflictSet`
whose constraint-group families include the expected culprit(s). A feasible
spec must return ``None``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tournament_scheduler.conflict import ConflictSet, extract_conflict
from tournament_scheduler.fixtures import small_tournament
from tournament_scheduler.models import (
    CoachingConflict,
    DivisionSpec,
    FieldSize,
    FieldSpec,
    TeamSpec,
    TimeWindow,
    TournamentSpec,
)
from tournament_scheduler.pools import assign_pools

DAY = datetime(2026, 9, 12)


# ---------------------------------------------------------------------------
# Golden infeasible specs (known true conflicts)
# ---------------------------------------------------------------------------


def rest_vs_availability_spec() -> TournamentSpec:
    """4 teams, 1 field, 6 games in a 2h window with a 120min rest requirement.

    Six 20min games fit the field's 2h window exactly (6 slots), but each team
    plays 3 games and 120min of rest between them cannot fit — a rest conflict.
    """
    div = DivisionSpec(
        id="u12",
        name="U12",
        field_size=FieldSize.MEDIUM,
        game_duration_minutes=20,
        halftime_minutes=0,
        buffer_minutes=0,
        min_rest_minutes=120,
        games_per_team=3,
        pool_size=4,
    )
    teams = [TeamSpec(id=f"t{i}", name=f"Team {i}", division_id="u12", seed=i) for i in range(1, 5)]
    fields = [
        FieldSpec(
            id="f1",
            name="Field 1",
            size=FieldSize.MEDIUM,
            availability=[TimeWindow(start=DAY.replace(hour=8), end=DAY.replace(hour=10))],
        )
    ]
    return TournamentSpec(name="RestConflict", divisions=[div], teams=teams, fields=fields, max_solve_seconds=10)


def too_few_field_hours_spec() -> TournamentSpec:
    """4 teams (6 games), 1 field, only 3 slots — a field-capacity conflict.

    A 90min window at 30min/game gives 3 slots on one field, but 6 games are
    required. No rest requirement, so the binding constraint is field capacity.
    """
    div = DivisionSpec(
        id="u12",
        name="U12",
        field_size=FieldSize.MEDIUM,
        game_duration_minutes=30,
        halftime_minutes=0,
        buffer_minutes=0,
        min_rest_minutes=0,
        games_per_team=3,
        pool_size=4,
    )
    teams = [TeamSpec(id=f"t{i}", name=f"Team {i}", division_id="u12", seed=i) for i in range(1, 5)]
    fields = [
        FieldSpec(
            id="f1",
            name="Field 1",
            size=FieldSize.MEDIUM,
            availability=[TimeWindow(start=DAY.replace(hour=8), end=DAY.replace(hour=9, minute=30))],
        )
    ]
    return TournamentSpec(name="FieldHours", divisions=[div], teams=teams, fields=fields, max_solve_seconds=10)


def coaching_simultaneity_spec() -> TournamentSpec:
    """Two divisions, one game each, only one (overlapping) slot per field.

    A coach shared across the two divisions' teams cannot attend both games,
    which are forced to overlap — a coaching conflict.
    """
    d1 = DivisionSpec(
        id="a",
        name="Div A",
        field_size=FieldSize.MEDIUM,
        game_duration_minutes=40,
        halftime_minutes=0,
        buffer_minutes=0,
        min_rest_minutes=0,
        games_per_team=1,
        pool_size=4,
    )
    d2 = DivisionSpec(
        id="b",
        name="Div B",
        field_size=FieldSize.LARGE,
        game_duration_minutes=40,
        halftime_minutes=0,
        buffer_minutes=0,
        min_rest_minutes=0,
        games_per_team=1,
        pool_size=4,
    )
    teams = [
        TeamSpec(id="a1", name="A1", division_id="a", seed=1),
        TeamSpec(id="a2", name="A2", division_id="a", seed=2),
        TeamSpec(id="b1", name="B1", division_id="b", seed=1),
        TeamSpec(id="b2", name="B2", division_id="b", seed=2),
    ]
    fields = [
        FieldSpec(
            id="fa",
            name="Field A",
            size=FieldSize.MEDIUM,
            availability=[TimeWindow(start=DAY.replace(hour=8), end=DAY.replace(hour=8, minute=40))],
        ),
        FieldSpec(
            id="fb",
            name="Field B",
            size=FieldSize.LARGE,
            availability=[TimeWindow(start=DAY.replace(hour=8), end=DAY.replace(hour=8, minute=40))],
        ),
    ]
    coaching = [CoachingConflict(coach_name="Coach X", team_ids=["a1", "b1"])]
    return TournamentSpec(
        name="CoachConflict",
        divisions=[d1, d2],
        teams=teams,
        fields=fields,
        coaching_conflicts=coaching,
        max_solve_seconds=10,
    )


def feasible_spec() -> TournamentSpec:
    """A comfortably-schedulable tiny tournament: 4 teams, 2 fields, 6h window."""
    div = DivisionSpec(
        id="u12",
        name="U12",
        field_size=FieldSize.MEDIUM,
        game_duration_minutes=30,
        halftime_minutes=0,
        buffer_minutes=0,
        min_rest_minutes=30,
        games_per_team=3,
        pool_size=4,
    )
    teams = [TeamSpec(id=f"t{i}", name=f"Team {i}", division_id="u12", seed=i) for i in range(1, 5)]
    fields = [
        FieldSpec(
            id=fid,
            name=fid,
            size=FieldSize.MEDIUM,
            availability=[TimeWindow(start=DAY.replace(hour=8), end=DAY.replace(hour=14))],
        )
        for fid in ("f1", "f2")
    ]
    return TournamentSpec(name="Feasible", divisions=[div], teams=teams, fields=fields, max_solve_seconds=10)


def _extract(spec: TournamentSpec) -> ConflictSet | None:
    return extract_conflict(spec, assign_pools(spec), time_limit_s=10.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRestVsAvailability:
    def test_conflict_extracted(self) -> None:
        cs = _extract(rest_vs_availability_spec())
        assert cs is not None
        # True culprit: a team's minimum-rest requirement vs the required games.
        assert "rest" in cs.groups()
        assert "assignment" in cs.groups()

    def test_rest_item_names_a_team(self) -> None:
        cs = _extract(rest_vs_availability_spec())
        assert cs is not None
        rest_items = [it for it in cs.involves if it.group == "rest"]
        assert rest_items, "expected at least one rest conflict item"
        # The rest descriptor should reference a real team in the division.
        team_ids = {f"t{i}" for i in range(1, 5)}
        assert any(set(it.spec_ids) & team_ids for it in rest_items)

    def test_is_minimal(self) -> None:
        cs = _extract(rest_vs_availability_spec())
        assert cs is not None
        assert cs.minimal is True
        assert cs.core_size == len(cs.involves)


class TestTooFewFieldHours:
    def test_conflict_extracted(self) -> None:
        cs = _extract(too_few_field_hours_spec())
        assert cs is not None
        # True culprit: field capacity cannot host all required games.
        assert "field_double_booking" in cs.groups()
        assert "assignment" in cs.groups()

    def test_field_item_names_the_field(self) -> None:
        cs = _extract(too_few_field_hours_spec())
        assert cs is not None
        field_items = [it for it in cs.involves if it.group == "field_double_booking"]
        assert field_items
        assert any("f1" in it.spec_ids for it in field_items)


class TestCoachingSimultaneity:
    def test_conflict_extracted(self) -> None:
        cs = _extract(coaching_simultaneity_spec())
        assert cs is not None
        # True culprit: the shared coach cannot attend both simultaneous games.
        assert "coaching" in cs.groups()
        assert "assignment" in cs.groups()

    def test_coaching_item_names_the_coach_teams(self) -> None:
        cs = _extract(coaching_simultaneity_spec())
        assert cs is not None
        coaching_items = [it for it in cs.involves if it.group == "coaching"]
        assert coaching_items
        assert any({"a1", "b1"} <= set(it.spec_ids) for it in coaching_items)

    def test_describe_and_json(self) -> None:
        cs = _extract(coaching_simultaneity_spec())
        assert cs is not None
        text = cs.describe()
        assert "cannot be scheduled" in text
        assert "Coach X" in text
        # to_json round-trips through pydantic.
        restored = ConflictSet.model_validate_json(cs.to_json())
        assert restored.groups() == cs.groups()


class TestFeasibleReturnsNone:
    def test_tiny_feasible_spec(self) -> None:
        assert _extract(feasible_spec()) is None

    def test_small_tournament_fixture(self) -> None:
        spec = small_tournament()
        assert extract_conflict(spec, assign_pools(spec), time_limit_s=10.0) is None


@pytest.mark.parametrize(
    "builder",
    [rest_vs_availability_spec, too_few_field_hours_spec, coaching_simultaneity_spec],
)
def test_summary_is_plain_english(builder) -> None:
    cs = _extract(builder())
    assert cs is not None
    assert cs.summary.startswith("The tournament cannot be scheduled")
    assert len(cs.involves) >= 1
