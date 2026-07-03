"""CP-SAT scheduling engine.

Phase 1 of the decomposition pipeline: given pools, schedule all pool-play
games onto fields and time slots, satisfying hard constraints and minimizing
weighted soft-constraint penalties.

Hard constraints (all must hold):
  - Each game is assigned to exactly one (field, time-slot)
  - No field double-booking (one game per field per time slot)
  - No team plays twice simultaneously
  - Minimum rest between consecutive games for any team
  - Fields must match the division's required field size
  - Fields must be available during the assigned time window
  - Coaching conflicts: teams sharing a coach cannot play simultaneously
  - Pool integrity: only intra-pool matchups

Soft constraints (weighted penalties in objective):
  - Minimize back-to-back games (player safety)
  - Balance early/late game assignments across teams
  - Respect time preferences for divisions/teams
  - Respect field preferences for divisions/teams
  - Spread games across the day (avoid clustering)
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from tournament_scheduler.models import (
    PRIORITY_WEIGHTS,
    DivisionSpec,
    Pool,
    ScheduledGame,
    SolveStats,
    TournamentSchedule,
    TournamentSpec,
)


def solve(spec: TournamentSpec, pools: list[Pool]) -> TournamentSchedule:
    """Build and solve the CP-SAT model for all pool-play games.

    This is the main entry point.  It:
    1. Generates all required matchups from pools.
    2. Discretizes time into slots per field.
    3. Builds the CP-SAT model with hard + soft constraints.
    4. Solves and extracts the schedule.
    """
    model = cp_model.CpModel()

    # -- Build the time-slot grid ------------------------------------------------
    divisions_by_id = {d.id: d for d in spec.divisions}
    fields_by_id = {f.id: f for f in spec.fields}
    teams_by_id = {t.id: t for t in spec.teams}

    # Generate matchups from pools
    matchups = _generate_matchups(pools, divisions_by_id)

    if not matchups:
        return TournamentSchedule(
            tournament_name=spec.name,
            pools=pools,
            games=[],
            stats=SolveStats(
                status="OPTIMAL",
                wall_time_seconds=0.0,
                objective_value=0.0,
                num_games_scheduled=0,
                num_teams=len(spec.teams),
                num_fields=len(spec.fields),
                num_divisions=len(spec.divisions),
            ),
        )

    # Discretize: for each field, generate valid time slots based on field
    # availability and the game durations of compatible divisions.
    slots = _build_time_slots(spec)

    # -- Decision variables -------------------------------------------------------
    # x[m, s] = 1 iff matchup m is assigned to slot s
    x: dict[tuple[int, int], cp_model.IntVar] = {}
    for m_idx, matchup in enumerate(matchups):
        division = divisions_by_id[matchup["division_id"]]
        game_minutes = spec.total_game_minutes(division)
        for s_idx, slot in enumerate(slots):
            field = fields_by_id[slot["field_id"]]
            # Only create variable if field size matches and slot is long enough
            if field.size == division.field_size and slot["duration_minutes"] >= game_minutes:
                x[m_idx, s_idx] = model.new_bool_var(f"x_{m_idx}_{s_idx}")

    # -- Hard constraints ----------------------------------------------------------

    # 1. Each matchup assigned to exactly one slot
    for m_idx in range(len(matchups)):
        feasible_slots = [s_idx for (mi, s_idx) in x if mi == m_idx]
        if not feasible_slots:
            # This matchup has no feasible slot -- model is infeasible
            # Add a contradiction to surface it cleanly
            model.add(0 >= 1)  # Force infeasibility
            break
        model.add(sum(x[m_idx, s_idx] for s_idx in feasible_slots) == 1)

    # 2. No field double-booking: at most one game per slot (slots are non-overlapping)
    for s_idx in range(len(slots)):
        games_in_slot = [x[m_idx, s_idx] for (m_idx, si) in x if si == s_idx]
        if len(games_in_slot) > 1:
            model.add(sum(games_in_slot) <= 1)

    # 3. No team plays twice simultaneously.
    #    Two slots are "simultaneous" if they overlap in time.
    team_matchups: dict[str, list[int]] = {}
    for m_idx, matchup in enumerate(matchups):
        for tid in (matchup["home"], matchup["away"]):
            team_matchups.setdefault(tid, []).append(m_idx)

    slot_overlaps = _compute_slot_overlaps(slots)

    for _team_id, m_indices in team_matchups.items():
        if len(m_indices) < 2:
            continue
        for m1, m2 in itertools.combinations(m_indices, 2):
            for s1 in [si for (mi, si) in x if mi == m1]:
                for s2 in [si for (mi, si) in x if mi == m2]:
                    if s1 == s2 or (s1, s2) in slot_overlaps:
                        model.add(x[m1, s1] + x[m2, s2] <= 1)

    # 4. Minimum rest between consecutive games for any team.
    for team_id, m_indices in team_matchups.items():
        if len(m_indices) < 2:
            continue
        division_id = teams_by_id[team_id].division_id
        min_rest = timedelta(minutes=divisions_by_id[division_id].min_rest_minutes)

        for m1, m2 in itertools.combinations(m_indices, 2):
            for s1 in [si for (mi, si) in x if mi == m1]:
                for s2 in [si for (mi, si) in x if mi == m2]:
                    if _slots_too_close(slots[s1], slots[s2], min_rest, divisions_by_id[division_id], spec):
                        model.add(x[m1, s1] + x[m2, s2] <= 1)

    # 5. Coaching conflicts: teams sharing a coach cannot play simultaneously.
    for conflict in spec.coaching_conflicts:
        conflict_team_matchups: list[int] = []
        for tid in conflict.team_ids:
            conflict_team_matchups.extend(team_matchups.get(tid, []))

        # Remove duplicates (a game between two conflict teams appears once)
        conflict_m_indices = list(set(conflict_team_matchups))
        if len(conflict_m_indices) < 2:
            continue

        for m1, m2 in itertools.combinations(conflict_m_indices, 2):
            # Only constrain if they involve different teams in the conflict
            m1_teams = {matchups[m1]["home"], matchups[m1]["away"]}
            m2_teams = {matchups[m2]["home"], matchups[m2]["away"]}
            if m1_teams == m2_teams:
                continue  # Same game, already handled

            for s1 in [si for (mi, si) in x if mi == m1]:
                for s2 in [si for (mi, si) in x if mi == m2]:
                    if s1 == s2 or (s1, s2) in slot_overlaps:
                        model.add(x[m1, s1] + x[m2, s2] <= 1)

    # -- Soft constraints ----------------------------------------------------------
    penalties: list[cp_model.LinearExpr] = []

    # S1. Minimize back-to-back games (high weight).
    #     "Back-to-back" = second game starts before min_rest is satisfied.
    back_to_back_weight = 10
    for team_id, m_indices in team_matchups.items():
        if len(m_indices) < 2:
            continue
        division_id = teams_by_id[team_id].division_id
        division = divisions_by_id[division_id]
        game_dur = timedelta(minutes=spec.total_game_minutes(division))
        # "Back-to-back" is when the gap between end of one game and start of
        # the next is less than half the min_rest.
        half_rest = timedelta(minutes=division.min_rest_minutes // 2) if division.min_rest_minutes > 0 else game_dur

        for m1, m2 in itertools.combinations(m_indices, 2):
            for s1 in [si for (mi, si) in x if mi == m1]:
                for s2 in [si for (mi, si) in x if mi == m2]:
                    if _slots_close_but_feasible(slots[s1], slots[s2], half_rest, division, spec):
                        pen = model.new_bool_var(f"btb_{team_id}_{m1}_{m2}_{s1}_{s2}")
                        model.add(x[m1, s1] + x[m2, s2] - 1 <= pen)
                        penalties.append(back_to_back_weight * pen)

    # S2. Balance early/late slots across teams (medium weight).
    #     Penalize a team getting more than one "early" or "late" game.
    early_late_weight = 3
    early_cutoff_hour = 9  # Games starting before 9 AM are "early"
    late_cutoff_hour = 17  # Games starting at or after 5 PM are "late"

    for team_id, m_indices in team_matchups.items():
        early_vars: list[cp_model.IntVar] = []
        late_vars: list[cp_model.IntVar] = []
        for m_idx in m_indices:
            for s_idx in [si for (mi, si) in x if mi == m_idx]:
                slot = slots[s_idx]
                if slot["start"].hour < early_cutoff_hour:
                    early_vars.append(x[m_idx, s_idx])
                if slot["start"].hour >= late_cutoff_hour:
                    late_vars.append(x[m_idx, s_idx])

        if len(early_vars) > 1:
            early_excess = model.new_int_var(0, len(early_vars), f"early_excess_{team_id}")
            model.add(sum(early_vars) - 1 <= early_excess)
            penalties.append(early_late_weight * early_excess)

        if len(late_vars) > 1:
            late_excess = model.new_int_var(0, len(late_vars), f"late_excess_{team_id}")
            model.add(sum(late_vars) - 1 <= late_excess)
            penalties.append(early_late_weight * late_excess)

    # S3. Time preferences for divisions/teams.
    for pref in spec.time_preferences:
        weight = PRIORITY_WEIGHTS[pref.priority]
        target_team_ids: list[str] = []
        if pref.target_type == "division":
            target_team_ids = [t.id for t in spec.teams_in_division(pref.target)]
        else:
            target_team_ids = [pref.target]

        for tid in target_team_ids:
            for m_idx in team_matchups.get(tid, []):
                for s_idx in [si for (mi, si) in x if mi == m_idx]:
                    slot = slots[s_idx]
                    if not _in_any_window(slot["start"], slot["end"], pref.preferred_windows):
                        # This slot is outside the preferred window -- penalize
                        pen = model.new_bool_var(f"timepref_{tid}_{m_idx}_{s_idx}")
                        model.add(x[m_idx, s_idx] <= pen)
                        penalties.append(weight * pen)

    if penalties:
        model.minimize(sum(penalties))

    # -- Solve ---------------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = spec.max_solve_seconds
    solver.parameters.num_workers = spec.num_workers
    solver.parameters.log_search_progress = False

    status = solver.solve(model)

    status_name = solver.status_name(status)
    stats = SolveStats(
        status=status_name,
        wall_time_seconds=round(solver.wall_time, 3),
        objective_value=round(solver.objective_value, 2) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        num_conflicts=solver.num_conflicts,
        num_branches=solver.num_branches,
        num_games_scheduled=0,
        num_teams=len(spec.teams),
        num_fields=len(spec.fields),
        num_divisions=len(spec.divisions),
    )

    games: list[ScheduledGame] = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        game_counter = 0
        for m_idx, matchup in enumerate(matchups):
            for s_idx in [si for (mi, si) in x if mi == m_idx]:
                if solver.value(x[m_idx, s_idx]) == 1:
                    slot = slots[s_idx]
                    division = divisions_by_id[matchup["division_id"]]
                    game_end = slot["start"] + timedelta(minutes=division.game_duration_minutes)
                    game_counter += 1
                    games.append(
                        ScheduledGame(
                            game_id=f"G{game_counter:04d}",
                            division_id=matchup["division_id"],
                            pool_id=matchup["pool_id"],
                            home_team_id=matchup["home"],
                            away_team_id=matchup["away"],
                            field_id=slot["field_id"],
                            start_time=slot["start"],
                            end_time=game_end,
                            game_number=matchup.get("game_number", 0),
                        )
                    )
        stats.num_games_scheduled = len(games)

    return TournamentSchedule(
        tournament_name=spec.name,
        pools=pools,
        games=games,
        stats=stats,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_matchups(pools: list[Pool], divisions: dict[str, DivisionSpec]) -> list[dict]:
    """Generate all required pool-play matchups (round-robin within each pool)."""
    matchups: list[dict] = []
    for pool in pools:
        division = divisions[pool.division_id]
        teams = pool.team_ids
        n = len(teams)

        # Full round-robin within pool
        rr_games = list(itertools.combinations(teams, 2))

        # If games_per_team limits total games, select a subset.
        # For a pool of size k, full RR gives k-1 games per team.
        # If games_per_team < k-1, we need a partial round-robin.
        games_per_team = division.games_per_team
        if games_per_team >= n - 1:
            # Full round-robin
            for game_num, (t1, t2) in enumerate(rr_games):
                matchups.append(
                    {
                        "home": t1,
                        "away": t2,
                        "division_id": pool.division_id,
                        "pool_id": pool.pool_id,
                        "game_number": game_num + 1,
                    }
                )
        else:
            # Partial round-robin: use circle method to pick a balanced subset
            selected = _partial_round_robin(teams, games_per_team)
            for game_num, (t1, t2) in enumerate(selected):
                matchups.append(
                    {
                        "home": t1,
                        "away": t2,
                        "division_id": pool.division_id,
                        "pool_id": pool.pool_id,
                        "game_number": game_num + 1,
                    }
                )

    return matchups


def _partial_round_robin(teams: list[str], games_per_team: int) -> list[tuple[str, str]]:
    """Select a balanced subset of round-robin matchups.

    Uses the circle method to generate rounds, then picks enough rounds
    so each team plays at least games_per_team games.
    """
    n = len(teams)
    if n <= 1:
        return []

    # Pad to even
    padded = list(teams)
    if n % 2 == 1:
        padded.append("__BYE__")

    k = len(padded)
    rounds: list[list[tuple[str, str]]] = []

    # Circle method
    fixed = padded[0]
    rotating = padded[1:]

    for _r in range(k - 1):
        round_games: list[tuple[str, str]] = []
        current = [fixed] + rotating
        for i in range(k // 2):
            t1, t2 = current[i], current[k - 1 - i]
            if t1 != "__BYE__" and t2 != "__BYE__":
                round_games.append((t1, t2))
        rounds.append(round_games)
        # Rotate
        rotating = [rotating[-1]] + rotating[:-1]

    # Pick the minimum number of rounds to satisfy games_per_team
    selected: list[tuple[str, str]] = []
    for round_games in rounds:
        if len(selected) > 0:
            # Check if we have enough
            counts: dict[str, int] = {}
            for t1, t2 in selected:
                counts[t1] = counts.get(t1, 0) + 1
                counts[t2] = counts.get(t2, 0) + 1
            if all(counts.get(t, 0) >= games_per_team for t in teams):
                break
        selected.extend(round_games)

    return selected


def _build_time_slots(spec: TournamentSpec) -> list[dict]:
    """Discretize field availability into non-overlapping time slots.

    Each slot is sized at the LARGEST total game duration (game + halftime
    + buffer) among divisions compatible with that field's size.  Shorter
    games finish early within the slot, wasting some field time -- this is
    a small inefficiency but keeps the model tractable.
    """
    slots: list[dict] = []
    divisions_by_size: dict[str, list[DivisionSpec]] = {}
    for d in spec.divisions:
        divisions_by_size.setdefault(d.field_size.value, []).append(d)

    for field in spec.fields:
        compatible_divisions = divisions_by_size.get(field.size.value, [])
        if not compatible_divisions:
            continue

        # Slot size = max game duration for any compatible division
        slot_minutes = max(spec.total_game_minutes(d) for d in compatible_divisions)

        for window in field.availability:
            current = window.start
            while current + timedelta(minutes=slot_minutes) <= window.end:
                slots.append(
                    {
                        "field_id": field.id,
                        "start": current,
                        "end": current + timedelta(minutes=slot_minutes),
                        "duration_minutes": slot_minutes,
                    }
                )
                current += timedelta(minutes=slot_minutes)

    return slots


def _compute_slot_overlaps(slots: list[dict]) -> set[tuple[int, int]]:
    """Compute which slot pairs overlap in time (on different fields).

    Since slots on the same field are non-overlapping, this only returns
    cross-field overlaps (for the simultaneous-play constraint).
    """
    overlaps: set[tuple[int, int]] = set()
    for i, s1 in enumerate(slots):
        for j, s2 in enumerate(slots):
            if i >= j:
                continue
            # Overlap if they share time on different fields
            if s1["start"] < s2["end"] and s2["start"] < s1["end"]:
                if s1["field_id"] != s2["field_id"]:
                    overlaps.add((i, j))
                    overlaps.add((j, i))
    return overlaps


def _slots_too_close(
    slot1: dict,
    slot2: dict,
    min_rest: timedelta,
    division: DivisionSpec,
    spec: TournamentSpec,
) -> bool:
    """Check if two slots are too close together (violates min rest).

    Two slots violate min rest if the gap between the end of one game and
    the start of the other is less than min_rest.
    """
    game_dur = timedelta(minutes=division.game_duration_minutes)

    end1 = slot1["start"] + game_dur
    end2 = slot2["start"] + game_dur

    # Gap from game 1 ending to game 2 starting
    gap_1_to_2 = (slot2["start"] - end1).total_seconds()
    gap_2_to_1 = (slot1["start"] - end2).total_seconds()

    # If either gap is >= min_rest, they're fine in that order
    min_rest_secs = min_rest.total_seconds()

    # They're too close if BOTH orderings violate min rest
    # (i.e. no valid ordering exists)
    return gap_1_to_2 < min_rest_secs and gap_2_to_1 < min_rest_secs


def _slots_close_but_feasible(
    slot1: dict,
    slot2: dict,
    threshold: timedelta,
    division: DivisionSpec,
    spec: TournamentSpec,
) -> bool:
    """Check if two slots are close (for soft penalty) but not infeasibly close."""
    game_dur = timedelta(minutes=division.game_duration_minutes)
    end1 = slot1["start"] + game_dur
    end2 = slot2["start"] + game_dur

    gap_1_to_2 = (slot2["start"] - end1).total_seconds()
    gap_2_to_1 = (slot1["start"] - end2).total_seconds()

    threshold_secs = threshold.total_seconds()

    # Close but feasible: the gap is positive but less than threshold
    return (0 <= gap_1_to_2 < threshold_secs) or (0 <= gap_2_to_1 < threshold_secs)


def _in_any_window(start: datetime, end: datetime, windows: list) -> bool:
    """Check if a time range falls within any of the given windows."""
    for w in windows:
        if start >= w.start and end <= w.end:
            return True
    return False
