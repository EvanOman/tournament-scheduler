"""Tests for pool assignment logic."""

from __future__ import annotations

from tournament_scheduler.fixtures import small_tournament
from tournament_scheduler.models import DivisionSpec, FieldSize, TeamSpec, TournamentSpec
from tournament_scheduler.pools import assign_pools


class TestPoolAssignment:
    def test_basic_pool_assignment(self):
        spec = small_tournament()
        pools = assign_pools(spec)

        # 3 divisions x 2 pools each (8 teams / pool_size 4) = 6 pools
        assert len(pools) == 6

        # Each pool has 4 teams
        for pool in pools:
            assert len(pool.team_ids) == 4

    def test_all_teams_assigned(self):
        spec = small_tournament()
        pools = assign_pools(spec)

        assigned_team_ids = set()
        for pool in pools:
            assigned_team_ids.update(pool.team_ids)

        all_team_ids = {t.id for t in spec.teams}
        assert assigned_team_ids == all_team_ids

    def test_no_team_in_multiple_pools(self):
        spec = small_tournament()
        pools = assign_pools(spec)

        seen: set[str] = set()
        for pool in pools:
            for tid in pool.team_ids:
                assert tid not in seen, f"Team {tid} appears in multiple pools"
                seen.add(tid)

    def test_serpentine_seeding(self):
        """Top seeds should be distributed across pools."""
        spec = small_tournament()
        pools = assign_pools(spec)

        # For U10 Boys (8 teams, 2 pools), seed 1 and seed 2 should be in different pools
        u10b_pools = [p for p in pools if p.division_id == "u10b"]
        assert len(u10b_pools) == 2

        # Seed 1 in pool A, seed 2 in pool B (serpentine)
        teams_by_id = {t.id: t for t in spec.teams}
        pool_a_seeds = sorted(
            [teams_by_id[tid].seed for tid in u10b_pools[0].team_ids if teams_by_id[tid].seed],
        )
        pool_b_seeds = sorted(
            [teams_by_id[tid].seed for tid in u10b_pools[1].team_ids if teams_by_id[tid].seed],
        )

        # Seeds 1,4,5,8 in one pool, 2,3,6,7 in the other (serpentine)
        assert pool_a_seeds == [1, 4, 5, 8]
        assert pool_b_seeds == [2, 3, 6, 7]

    def test_uneven_division(self):
        """A division with teams not evenly divisible by pool_size."""
        from datetime import datetime

        from tournament_scheduler.models import FieldSpec, TimeWindow

        spec = TournamentSpec(
            name="Test",
            divisions=[
                DivisionSpec(
                    id="d1",
                    name="U12",
                    field_size=FieldSize.LARGE,
                    game_duration_minutes=30,
                    pool_size=4,
                    games_per_team=3,
                ),
            ],
            teams=[TeamSpec(id=f"t{i}", name=f"Team {i}", division_id="d1") for i in range(10)],
            fields=[
                FieldSpec(
                    id="f1",
                    name="Field 1",
                    size=FieldSize.LARGE,
                    availability=[
                        TimeWindow(start=datetime(2026, 9, 12, 8), end=datetime(2026, 9, 12, 18)),
                    ],
                ),
            ],
        )

        pools = assign_pools(spec)

        # 10 teams / pool_size 4 -> ceil(10/4) = 3 pools
        assert len(pools) == 3

        # All teams assigned
        total = sum(len(p.team_ids) for p in pools)
        assert total == 10
