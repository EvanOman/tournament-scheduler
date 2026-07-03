"""Tests for the CP-SAT solver.

These tests verify that the solver produces valid schedules that satisfy
all hard constraints. They use the synthetic fixtures at increasing scale.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from tournament_scheduler.fixtures import large_tournament, medium_tournament, small_tournament
from tournament_scheduler.models import TournamentSpec
from tournament_scheduler.pools import assign_pools
from tournament_scheduler.solver import solve
from tournament_scheduler.validator import validate


class TestSmallTournament:
    """24 teams, 3 divisions, 4 fields, single day."""

    @pytest.fixture()
    def spec(self) -> TournamentSpec:
        return small_tournament()

    @pytest.fixture()
    def schedule(self, spec):
        pools = assign_pools(spec)
        return solve(spec, pools)

    def test_solver_finds_solution(self, schedule):
        assert schedule.stats.status in ("OPTIMAL", "FEASIBLE")

    def test_all_games_scheduled(self, spec, schedule):
        # 3 divisions x 2 pools x C(4,2)=6 games per pool = 36 games
        expected_games = 36
        assert schedule.stats.num_games_scheduled == expected_games

    def test_validation_passes(self, spec, schedule):
        result = validate(schedule, spec)
        assert result.valid, result.summary()

    def test_no_field_double_booking(self, spec, schedule):
        """Verify independently that no field has overlapping games."""
        for field in spec.fields:
            games = schedule.games_on_field(field.id)
            for i, g1 in enumerate(games):
                for g2 in games[i + 1 :]:
                    assert not (g1.start_time < g2.end_time and g2.start_time < g1.end_time), (
                        f"Double booking on {field.id}: {g1.game_id} and {g2.game_id}"
                    )

    def test_no_team_plays_twice_simultaneously(self, spec, schedule):
        """Verify no team has overlapping games."""
        for team in spec.teams:
            games = schedule.games_for_team(team.id)
            for i, g1 in enumerate(games):
                for g2 in games[i + 1 :]:
                    assert not (g1.start_time < g2.end_time and g2.start_time < g1.end_time), (
                        f"Team {team.id} plays simultaneously: {g1.game_id} and {g2.game_id}"
                    )

    def test_minimum_rest_respected(self, spec, schedule):
        """Verify minimum rest between consecutive games."""
        divisions_by_id = {d.id: d for d in spec.divisions}
        for team in spec.teams:
            division = divisions_by_id[team.division_id]
            min_rest = timedelta(minutes=division.min_rest_minutes)
            games = schedule.games_for_team(team.id)
            for i in range(len(games) - 1):
                gap = games[i + 1].start_time - games[i].end_time
                assert gap >= min_rest, (
                    f"Team {team.id}: only {gap} rest between "
                    f"{games[i].game_id} and {games[i + 1].game_id} (need {min_rest})"
                )

    def test_field_sizes_match(self, spec, schedule):
        """Verify games are on correctly-sized fields."""
        divisions_by_id = {d.id: d for d in spec.divisions}
        fields_by_id = {f.id: f for f in spec.fields}
        for game in schedule.games:
            division = divisions_by_id[game.division_id]
            field = fields_by_id[game.field_id]
            assert field.size == division.field_size, (
                f"Game {game.game_id}: division {division.name} needs {division.field_size}, "
                f"but field {field.name} is {field.size}"
            )

    def test_coaching_conflict_respected(self, spec, schedule):
        """Verify the coaching conflict between u10b_team_01 and u12g_team_01."""
        games_t1 = schedule.games_for_team("u10b_team_01")
        games_t2 = schedule.games_for_team("u12g_team_01")
        for g1 in games_t1:
            for g2 in games_t2:
                assert not (g1.start_time < g2.end_time and g2.start_time < g1.end_time), (
                    f"Coaching conflict violated: {g1.game_id} and {g2.game_id} overlap"
                )

    def test_each_team_plays_3_games(self, spec, schedule):
        """Every team should play exactly 3 pool-play games."""
        for team in spec.teams:
            games = schedule.games_for_team(team.id)
            assert len(games) == 3, f"Team {team.id} has {len(games)} games, expected 3"

    def test_solve_time_reasonable(self, spec):
        """Solver should complete in under 30 seconds for small tournament."""
        pools = assign_pools(spec)
        start = time.monotonic()
        schedule = solve(spec, pools)
        elapsed = time.monotonic() - start
        assert schedule.stats.status in ("OPTIMAL", "FEASIBLE")
        assert elapsed < 30, f"Solve took {elapsed:.1f}s, expected < 30s"


class TestMediumTournament:
    """48 teams, 5 divisions, 6 fields, weekend."""

    @pytest.fixture()
    def spec(self) -> TournamentSpec:
        return medium_tournament()

    @pytest.fixture()
    def schedule(self, spec):
        pools = assign_pools(spec)
        return solve(spec, pools)

    def test_solver_finds_solution(self, schedule):
        assert schedule.stats.status in ("OPTIMAL", "FEASIBLE")

    def test_validation_passes(self, spec, schedule):
        result = validate(schedule, spec)
        assert result.valid, result.summary()

    def test_game_count(self, spec, schedule):
        # 2 divisions of 8 teams = 2x2 pools x 6 games = 24
        # 2 divisions of 12 teams = 2x3 pools x 6 games = 36
        # 1 division of 8 teams = 2 pools x 6 games = 12
        # Total: 24 + 36 + 12 = 72 -- wait, let me recalculate
        # u10b: 8 teams, pool_size=4, 2 pools, 6 games each = 12
        # u10g: 8 teams, pool_size=4, 2 pools, 6 games each = 12
        # u12b: 12 teams, pool_size=4, 3 pools, 6 games each = 18
        # u12g: 8 teams, pool_size=4, 2 pools, 6 games each = 12
        # u14b: 12 teams, pool_size=4, 3 pools, 6 games each = 18
        # Total: 12+12+18+12+18 = 72
        assert schedule.stats.num_games_scheduled == 72

    def test_all_constraints_satisfied(self, spec, schedule):
        """Comprehensive constraint check on medium tournament."""
        result = validate(schedule, spec)
        assert result.valid, result.summary()
        # No errors at all
        assert len(result.errors) == 0

    def test_solve_time_reasonable(self, spec):
        """Solver should complete within the time budget."""
        pools = assign_pools(spec)
        start = time.monotonic()
        schedule = solve(spec, pools)
        elapsed = time.monotonic() - start
        assert schedule.stats.status in ("OPTIMAL", "FEASIBLE")
        assert elapsed < 120, f"Solve took {elapsed:.1f}s, expected < 120s"


class TestLargeTournament:
    """96 teams, 8 divisions, 10 fields, weekend."""

    @pytest.fixture()
    def spec(self) -> TournamentSpec:
        return large_tournament()

    @pytest.fixture()
    def schedule(self, spec):
        pools = assign_pools(spec)
        return solve(spec, pools)

    def test_solver_finds_solution(self, schedule):
        assert schedule.stats.status in ("OPTIMAL", "FEASIBLE")

    def test_validation_passes(self, spec, schedule):
        result = validate(schedule, spec)
        assert result.valid, result.summary()

    def test_game_count(self, spec, schedule):
        # 8 divisions x 12 teams x pool_size=4 = 3 pools each
        # Each pool: C(4,2) = 6 games
        # 8 x 3 x 6 = 144 games
        assert schedule.stats.num_games_scheduled == 144

    def test_all_teams_play_correct_count(self, spec, schedule):
        """Each team plays exactly 3 games."""
        for team in spec.teams:
            games = schedule.games_for_team(team.id)
            assert len(games) == 3, f"Team {team.id} has {len(games)} games"
