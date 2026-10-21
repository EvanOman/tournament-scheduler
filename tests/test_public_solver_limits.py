"""Bound public model-construction work before calling the solver."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tournament_scheduler.models import (
    CoachingConflict,
    DivisionSpec,
    FieldSize,
    FieldSpec,
    SolveStats,
    TeamSpec,
    TimePreference,
    TimeWindow,
    TournamentSchedule,
    TournamentSpec,
)
from tournament_scheduler.pools import assign_pools
from tourneydesk import solver_limits
from tourneydesk.core import service
from tourneydesk.limits import PUBLIC_DEMO_LIMITS
from tourneydesk.session import SpecSession
from tourneydesk.tools import dispatch

_MESSAGE = "Demo workload too large; reduce teams, fields or availability."


def _spec(
    divisions: tuple[tuple[int, int, int, int], ...] = ((4, 40, 1, 60),),
    fields: tuple[tuple[int, int], ...] = ((9, 19),),
    *,
    days: int = 1,
    buffer: int = 10,
    pool_size: int = 4,
) -> TournamentSpec:
    division_specs = []
    teams = []
    for index, (count, game_minutes, games, rest) in enumerate(divisions, 1):
        division_id = f"d{index}"
        division_specs.append(
            DivisionSpec(
                id=division_id,
                name=division_id,
                field_size=FieldSize.FULL,
                game_duration_minutes=game_minutes,
                games_per_team=games,
                min_rest_minutes=rest,
                buffer_minutes=buffer,
                pool_size=pool_size,
                bracket_after_pools=False,
            )
        )
        teams.extend(
            TeamSpec(id=f"{division_id}_t{n}", name=f"Team {n}", division_id=division_id) for n in range(1, count + 1)
        )
    first_day = datetime(2027, 6, 12)
    return TournamentSpec(
        name="Synthetic public tournament",
        divisions=division_specs,
        teams=teams,
        fields=[
            FieldSpec(
                id=f"f{index}",
                name=f"Field {index}",
                size=FieldSize.FULL,
                availability=[
                    TimeWindow(
                        start=first_day + timedelta(days=day, hours=start),
                        end=first_day + timedelta(days=day, hours=end),
                    )
                    for day in range(days)
                ],
            )
            for index, (start, end) in enumerate(fields, 1)
        ],
    )


def _session(spec: TournamentSpec, *, limited: bool = True) -> SpecSession:
    session = SpecSession(limits=PUBLIC_DEMO_LIMITS if limited else None)
    for division in spec.divisions:
        session.add_division(**division.model_dump(mode="json"), source_quote="q")
        session.add_teams(
            division_id=division.id,
            teams=[
                team.model_dump(mode="json", exclude={"division_id"}) for team in spec.teams_in_division(division.id)
            ],
            source_quote="q",
        )
    for field in spec.fields:
        session.add_field(**field.model_dump(mode="json"), source_quote="q")
    return session


def _extreme_spec(game_minutes: int = 10) -> TournamentSpec:
    return _spec(
        ((24, game_minutes, 5, 180), (24, game_minutes, 5, 180), (16, game_minutes, 5, 180)),
        ((0, 24),) * 8,
        days=2,
        buffer=0,
        pool_size=8,
    )


@pytest.mark.parametrize("game_minutes", [10, 15])
def test_accepted_entity_caps_cannot_trigger_an_excessive_model(game_minutes: int) -> None:
    spec = _extreme_spec(game_minutes)
    with pytest.raises(ValueError, match=_MESSAGE):
        solver_limits.validate_public_solver_work(spec, assign_pools(spec))


def test_public_core_rejects_before_calling_the_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_solve(*args, **kwargs):
        pytest.fail("an excessive public draft must not reach solver construction")

    monkeypatch.setattr(service, "solve", unexpected_solve)
    session = _session(_extreme_spec())
    with pytest.raises(ValueError, match=_MESSAGE):
        service.solve_current(session)
    assert session.solve_cache is None


def test_schedule_summary_tool_cannot_bypass_the_work_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_solve(*args, **kwargs):
        pytest.fail("the schedule-summary tool must not reach an excessive model")

    monkeypatch.setattr(service, "solve", unexpected_solve)
    result = dispatch(_session(_extreme_spec()), "get_schedule_summary", {})
    assert result.is_error
    assert result.content == _MESSAGE


def test_ordinary_sessions_keep_their_unrestricted_solver_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def stub_solve(spec, pools):
        calls.append((spec, pools))
        return TournamentSchedule(
            tournament_name=spec.name,
            pools=pools,
            games=[],
            stats=SolveStats(status="INFEASIBLE", wall_time_seconds=0),
        )

    monkeypatch.setattr(service, "solve", stub_solve)
    outcome = service.solve_current(_session(_extreme_spec(), limited=False))
    assert outcome.status == "infeasible"
    assert len(calls) == 1
    assert len(calls[0][0].teams) == 64


# Mirrors DEFAULT_STATE and every SCENARIO_PRESETS state in the site's
# src/lab/tourneydesk/config.ts, without importing the HTTP API or building CP.
@pytest.mark.parametrize(
    "name, divisions, fields, days",
    [
        ("default", ((6, 40, 2, 30), (6, 45, 2, 30)), ((9, 17), (9, 17)), 1),
        ("saturday-rec", ((6, 40, 2, 30), (6, 45, 2, 30)), ((9, 17), (9, 17)), 1),
        ("impossible", ((10, 45, 3, 90),), ((9, 15), (9, 15)), 1),
        ("packed-weekend", ((8, 40, 3, 45), (8, 45, 3, 45), (6, 50, 3, 45)), ((8, 18), (8, 18), (9, 17)), 2),
    ],
)
def test_published_defaults_and_every_preset_fit(name, divisions, fields, days) -> None:
    spec = _spec(divisions, fields, days=days)
    if name == "packed-weekend":
        spec.coaching_conflicts = [CoachingConflict(coach_name="Rivera", team_ids=["d1_t1", "d1_t2"])]
    solver_limits.validate_public_solver_work(spec, assign_pools(spec))


@pytest.mark.parametrize("teams", [4, 8])
def test_small_twelve_hour_two_field_events_fit(teams: int) -> None:
    spec = _spec(((teams, 30, 3, 60),), ((8, 20), (8, 20)))
    solver_limits.validate_public_solver_work(spec, assign_pools(spec))


def test_existing_fake_chat_shape_still_solves_as_a_public_session() -> None:
    # Same 4-team/40-minute/one-game/one-field shape as _full_spec_model.
    outcome = service.solve_current(_session(_spec(fields=((9, 17),))))
    assert outcome.status == "solved"
    assert outcome.validation is not None and outcome.validation.valid


def test_guard_never_enumerates_slots_or_matchups(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_enumeration(*args, **kwargs):
        pytest.fail("the guard must use bounded arithmetic before slot or matchup enumeration")

    monkeypatch.setattr("tournament_scheduler.solver._build_time_slots", unexpected_enumeration)
    monkeypatch.setattr("tournament_scheduler.solver._generate_matchups", unexpected_enumeration)
    spec = _extreme_spec()
    with pytest.raises(ValueError, match=_MESSAGE):
        solver_limits.validate_public_solver_work(spec, assign_pools(spec))


def test_coaching_pair_work_is_part_of_the_budget() -> None:
    spec = _spec(((8, 10, 3, 30),), ((7, 22),), buffer=0)
    pools = assign_pools(spec)
    solver_limits.validate_public_solver_work(spec, pools)
    spec.coaching_conflicts = [
        CoachingConflict(coach_name=f"Coach {n}", team_ids=[f"d1_t{team}" for team in range(1, 5)]) for n in range(4)
    ]
    with pytest.raises(ValueError, match=_MESSAGE):
        solver_limits.validate_public_solver_work(spec, pools)


def test_variable_scan_budget_is_checked_at_its_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two matchups and twelve slots: 24 variable candidates + 12*24 field scans.
    spec = _spec()
    pools = assign_pools(spec)
    monkeypatch.setattr(solver_limits, "MAX_PUBLIC_VARIABLE_SCANS", 311)
    with pytest.raises(ValueError, match=_MESSAGE):
        solver_limits.validate_public_solver_work(spec, pools)
    monkeypatch.setattr(solver_limits, "MAX_PUBLIC_VARIABLE_SCANS", 312)
    solver_limits.validate_public_solver_work(spec, pools)


def test_all_three_team_pair_passes_are_budgeted(monkeypatch: pytest.MonkeyPatch) -> None:
    # 12*12 overlap checks + 4 teams * 3 game-pairs * 12*12 slots * 3 passes.
    spec = _spec(((4, 40, 3, 60),))
    pools = assign_pools(spec)
    monkeypatch.setattr(solver_limits, "MAX_PUBLIC_PAIR_ITERATIONS", 5327)
    with pytest.raises(ValueError, match=_MESSAGE):
        solver_limits.validate_public_solver_work(spec, pools)
    monkeypatch.setattr(solver_limits, "MAX_PUBLIC_PAIR_ITERATIONS", 5328)
    solver_limits.validate_public_solver_work(spec, pools)


def test_time_preferences_add_to_the_soft_scan_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec()
    pools = assign_pools(spec)
    # Early/late scans: 4 team-game appearances * 24 variables = 96.
    monkeypatch.setattr(solver_limits, "MAX_PUBLIC_SOFT_SCANS", 96)
    solver_limits.validate_public_solver_work(spec, pools)
    spec.time_preferences = [TimePreference(target="d1", preferred_windows=list(spec.fields[0].availability))]
    with pytest.raises(ValueError, match=_MESSAGE):
        solver_limits.validate_public_solver_work(spec, pools)


def test_cardinality_is_bounded_before_processing_untrusted_pool_members() -> None:
    spec = _spec()
    pools = assign_pools(spec)
    pools[0].team_ids *= 100
    with pytest.raises(ValueError, match=_MESSAGE):
        solver_limits.validate_public_solver_work(spec, pools)
