"""Pool assignment logic.

Phase 0 of the decomposition pipeline: assign teams to pools within each
division using serpentine seeding.  This is a small combinatorial problem
solvable by simple heuristics -- no solver needed.
"""

from __future__ import annotations

import math

from tournament_scheduler.models import DivisionSpec, Pool, TeamSpec, TournamentSpec


def assign_pools(spec: TournamentSpec) -> list[Pool]:
    """Assign teams to pools for every division in the tournament.

    Uses serpentine (snake) seeding when seeds are provided:
    Pool A gets seed 1, Pool B gets seed 2, ..., then the order reverses.
    Unseeded teams are appended in registration order.
    """
    all_pools: list[Pool] = []

    for division in spec.divisions:
        teams = spec.teams_in_division(division.id)
        div_pools = _assign_division_pools(division, teams)
        all_pools.extend(div_pools)

    return all_pools


def _assign_division_pools(division: DivisionSpec, teams: list[TeamSpec]) -> list[Pool]:
    """Create pools for a single division."""
    n_teams = len(teams)
    pool_size = division.pool_size

    if n_teams < pool_size:
        # Fewer teams than a single pool -- just one pool
        return [
            Pool(
                pool_id=f"{division.id}_pool_A",
                division_id=division.id,
                team_ids=[t.id for t in teams],
            )
        ]

    n_pools = math.ceil(n_teams / pool_size)

    # Sort teams by seed (seeded first, then unseeded in original order)
    seeded = sorted([t for t in teams if t.seed is not None], key=lambda t: t.seed)  # type: ignore[arg-type]
    unseeded = [t for t in teams if t.seed is None]
    ordered = seeded + unseeded

    # Serpentine assignment
    pool_assignments: list[list[str]] = [[] for _ in range(n_pools)]
    for i, team in enumerate(ordered):
        cycle = i // n_pools
        pool_idx = i % n_pools
        if cycle % 2 == 1:
            pool_idx = n_pools - 1 - pool_idx
        pool_assignments[pool_idx].append(team.id)

    pool_labels = _pool_labels(n_pools)
    return [
        Pool(
            pool_id=f"{division.id}_pool_{label}",
            division_id=division.id,
            team_ids=team_ids,
        )
        for label, team_ids in zip(pool_labels, pool_assignments, strict=True)
    ]


def _pool_labels(n: int) -> list[str]:
    """Generate pool labels: A, B, C, ..., Z, AA, AB, ..."""
    labels: list[str] = []
    for i in range(n):
        if i < 26:
            labels.append(chr(65 + i))
        else:
            labels.append(chr(65 + i // 26 - 1) + chr(65 + i % 26))
    return labels
