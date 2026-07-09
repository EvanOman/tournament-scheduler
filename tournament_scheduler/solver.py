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

Instrumented mode (M5 infeasibility engine)
--------------------------------------------
`build_model(spec, pools, instrument=True)` registers every hard-constraint
*group* behind a CP-SAT assumption literal (a ``BoolVar``) enforced via
``.only_enforce_if(lit)``.  Asserting a literal true (through
``model.add_assumptions``) activates that group; dropping it deactivates the
group.  This lets `conflict.extract_conflict` compute a minimal unsat core
(minimal infeasible subset of constraint groups) when a spec is infeasible.
The fast, no-assumption path (``instrument=False``) is the default and is used
by `solve` — it produces exactly the same model as before this feature.

Grouping scheme (one assumption literal per key):
  - ``assignment`` — per matchup: "this matchup must occupy exactly one slot".
  - ``availability`` — per matchup with *no* compatible field/slot: a guarded
    infeasibility marker so "matchup X cannot be placed anywhere" surfaces as
    an extractable conflict (replaces the old ``model.add(0 >= 1)`` hack).
  - ``field_double_booking`` — per field: "no two games share a slot here".
  - ``team_simultaneous`` — per team: "this team never plays two overlapping
    games".
  - ``rest`` — per team: "minimum rest is honoured between this team's games".
  - ``coaching`` — per coaching conflict (coach): "this coach's teams never
    play simultaneously".

Granularity is per-(family, entity) wherever cheap so an extracted core points
at a specific, explainable culprit (a named team / coach / field / matchup)
without exploding the literal count.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
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

# ---------------------------------------------------------------------------
# Instrumentation types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupDescriptor:
    """Human-readable descriptor for one instrumented hard-constraint group.

    Attached to the assumption literal that guards the group so that, when the
    group appears in an unsat core, the conflict can be explained in plain
    English and traced back to the spec objects involved.
    """

    group: str  # constraint family, e.g. "rest", "coaching", "field_double_booking"
    descriptor: str  # human-readable one-liner, e.g. "Minimum rest 90min for team u14b_team_03"
    spec_ids: tuple[str, ...]  # relevant spec object ids (team/field/division/pool ids, coach name)


@dataclass
class BuiltModel:
    """A constructed CP-SAT model plus the metadata needed to interpret it."""

    model: cp_model.CpModel
    x: dict[tuple[int, int], cp_model.IntVar]
    matchups: list[dict]
    slots: list[dict]
    team_matchups: dict[str, list[int]]
    slot_overlaps: set[tuple[int, int]]
    divisions_by_id: dict[str, DivisionSpec]
    fields_by_id: dict
    teams_by_id: dict
    # Populated only when instrument=True. ``assumption_lits`` are the group
    # literals to assert; ``assumptions`` maps each literal's index (CP-SAT
    # ``IntVar`` is not hashable) to its human-readable descriptor.
    assumption_lits: list[cp_model.IntVar] = field(default_factory=list)
    assumptions: dict[int, GroupDescriptor] = field(default_factory=dict)


class _GroupRegistry:
    """Lazily mints one assumption literal per constraint-group key.

    When ``instrument`` is False the registry is a no-op: ``lit`` returns
    ``None`` and callers add their constraints unguarded (the fast path).
    """

    def __init__(self, model: cp_model.CpModel, instrument: bool) -> None:
        self._model = model
        self._instrument = instrument
        self._lits: dict[str, cp_model.IntVar] = {}
        self.lits: list[cp_model.IntVar] = []
        self.assumptions: dict[int, GroupDescriptor] = {}

    def lit(
        self,
        key: str,
        *,
        group: str,
        descriptor: str,
        spec_ids: tuple[str, ...],
    ) -> cp_model.IntVar | None:
        """Return the assumption literal for ``key`` (creating it once), or None."""
        if not self._instrument:
            return None
        existing = self._lits.get(key)
        if existing is not None:
            return existing
        var = self._model.new_bool_var(f"assume__{key}")
        self._lits[key] = var
        self.lits.append(var)
        self.assumptions[var.index] = GroupDescriptor(group=group, descriptor=descriptor, spec_ids=spec_ids)
        return var


def _guard(constraint: cp_model.Constraint, lit: cp_model.IntVar | None) -> None:
    """Attach ``constraint`` to assumption literal ``lit`` when instrumenting."""
    if lit is not None:
        constraint.only_enforce_if(lit)


# ---------------------------------------------------------------------------
# Model construction
# ---------------------------------------------------------------------------


def build_model(spec: TournamentSpec, pools: list[Pool], *, instrument: bool = False) -> BuiltModel:
    """Construct the CP-SAT model (decision vars + all hard constraints).

    When ``instrument`` is False (default, used by `solve`) the model is built
    exactly as before: constraints are added unguarded and ``assumptions`` is
    empty.  When ``instrument`` is True, each hard-constraint group is guarded
    by an assumption literal and recorded in ``assumptions`` for conflict
    extraction.  Soft constraints and the objective are NOT added here; `solve`
    layers those on top of the returned model for the fast path only.
    """
    model = cp_model.CpModel()
    reg = _GroupRegistry(model, instrument)

    divisions_by_id = {d.id: d for d in spec.divisions}
    fields_by_id = {f.id: f for f in spec.fields}
    teams_by_id = {t.id: t for t in spec.teams}

    matchups = _generate_matchups(pools, divisions_by_id)
    slots = _build_time_slots(spec)

    # -- Decision variables ---------------------------------------------------
    # x[m, s] = 1 iff matchup m is assigned to slot s.  A variable exists only
    # when the field size matches the division and the slot is long enough:
    # this is how field-size / availability compatibility is encoded.
    x: dict[tuple[int, int], cp_model.IntVar] = {}
    for m_idx, matchup in enumerate(matchups):
        division = divisions_by_id[matchup["division_id"]]
        game_minutes = spec.total_game_minutes(division)
        for s_idx, slot in enumerate(slots):
            fld = fields_by_id[slot["field_id"]]
            if fld.size == division.field_size and slot["duration_minutes"] >= game_minutes:
                x[m_idx, s_idx] = model.new_bool_var(f"x_{m_idx}_{s_idx}")

    # Precompute per-matchup feasible slots.
    slots_for_matchup: dict[int, list[int]] = {m_idx: [] for m_idx in range(len(matchups))}
    for mi, s_idx in x:
        slots_for_matchup[mi].append(s_idx)

    team_matchups: dict[str, list[int]] = {}
    for m_idx, matchup in enumerate(matchups):
        for tid in (matchup["home"], matchup["away"]):
            team_matchups.setdefault(tid, []).append(m_idx)

    slot_overlaps = _compute_slot_overlaps(slots)

    # -- Hard constraint 1: each matchup assigned to exactly one slot ----------
    for m_idx, matchup in enumerate(matchups):
        feasible = slots_for_matchup[m_idx]
        div = divisions_by_id[matchup["division_id"]]
        label = f"{matchup['home']} vs {matchup['away']} ({div.name})"
        if not feasible:
            # No compatible field / availability window: an extractable
            # "cannot be placed anywhere" conflict (availability family).
            if instrument:
                lit = reg.lit(
                    f"availability:{m_idx}",
                    group="availability",
                    descriptor=(
                        f"Matchup {label} cannot be placed on any field: no field of size "
                        f"{div.field_size.value} is available long enough for a {spec.total_game_minutes(div)}min game"
                    ),
                    spec_ids=(matchup["home"], matchup["away"], matchup["division_id"], matchup["pool_id"]),
                )
                _guard(model.add(0 >= 1), lit)
            else:
                # Fast path: surface infeasibility cleanly and stop.
                model.add(0 >= 1)
                break
            continue
        lit = reg.lit(
            f"assignment:{m_idx}",
            group="assignment",
            descriptor=f"Matchup {label} must be scheduled in exactly one time slot",
            spec_ids=(matchup["home"], matchup["away"], matchup["division_id"], matchup["pool_id"]),
        )
        _guard(model.add(sum(x[m_idx, s_idx] for s_idx in feasible) == 1), lit)

    # -- Hard constraint 2: no field double-booking (one game per slot) --------
    # Grouped per field so a capacity conflict localises to a named field.
    slots_by_field: dict[str, list[int]] = {}
    for s_idx, slot in enumerate(slots):
        slots_by_field.setdefault(slot["field_id"], []).append(s_idx)

    for field_id, field_slot_indices in slots_by_field.items():
        fld = fields_by_id[field_id]
        lit = reg.lit(
            f"field_double_booking:{field_id}",
            group="field_double_booking",
            descriptor=f"Field {fld.name} can host at most one game per time slot",
            spec_ids=(field_id,),
        )
        for s_idx in field_slot_indices:
            games_in_slot = [x[m_idx, si] for (m_idx, si) in x if si == s_idx]
            if len(games_in_slot) > 1:
                _guard(model.add(sum(games_in_slot) <= 1), lit)

    # -- Hard constraint 3: no team plays twice simultaneously -----------------
    for team_id, m_indices in team_matchups.items():
        if len(m_indices) < 2:
            continue
        lit = reg.lit(
            f"team_simultaneous:{team_id}",
            group="team_simultaneous",
            descriptor=f"Team {teams_by_id[team_id].name} cannot play two games at overlapping times",
            spec_ids=(team_id,),
        )
        for m1, m2 in itertools.combinations(m_indices, 2):
            for s1 in slots_for_matchup[m1]:
                for s2 in slots_for_matchup[m2]:
                    if s1 == s2 or (s1, s2) in slot_overlaps:
                        _guard(model.add(x[m1, s1] + x[m2, s2] <= 1), lit)

    # -- Hard constraint 4: minimum rest between a team's games ----------------
    for team_id, m_indices in team_matchups.items():
        if len(m_indices) < 2:
            continue
        division_id = teams_by_id[team_id].division_id
        division = divisions_by_id[division_id]
        min_rest = timedelta(minutes=division.min_rest_minutes)
        if division.min_rest_minutes <= 0:
            continue
        lit = reg.lit(
            f"rest:{team_id}",
            group="rest",
            descriptor=f"Minimum rest {division.min_rest_minutes}min for team {teams_by_id[team_id].name}",
            spec_ids=(team_id, division_id),
        )
        for m1, m2 in itertools.combinations(m_indices, 2):
            for s1 in slots_for_matchup[m1]:
                for s2 in slots_for_matchup[m2]:
                    if _slots_too_close(slots[s1], slots[s2], min_rest, division, spec):
                        _guard(model.add(x[m1, s1] + x[m2, s2] <= 1), lit)

    # -- Hard constraint 5: coaching conflicts (shared coach) ------------------
    for conflict in spec.coaching_conflicts:
        conflict_team_matchups: list[int] = []
        for tid in conflict.team_ids:
            conflict_team_matchups.extend(team_matchups.get(tid, []))
        conflict_m_indices = list(set(conflict_team_matchups))
        if len(conflict_m_indices) < 2:
            continue
        lit = reg.lit(
            f"coaching:{conflict.coach_name}",
            group="coaching",
            descriptor=(
                f"Coaching conflict: {conflict.coach_name} coaches "
                f"{', '.join(conflict.team_ids)} and cannot be in two places at once"
            ),
            spec_ids=tuple(conflict.team_ids),
        )
        for m1, m2 in itertools.combinations(conflict_m_indices, 2):
            m1_teams = {matchups[m1]["home"], matchups[m1]["away"]}
            m2_teams = {matchups[m2]["home"], matchups[m2]["away"]}
            if m1_teams == m2_teams:
                continue  # Same game, already handled
            for s1 in slots_for_matchup[m1]:
                for s2 in slots_for_matchup[m2]:
                    if s1 == s2 or (s1, s2) in slot_overlaps:
                        _guard(model.add(x[m1, s1] + x[m2, s2] <= 1), lit)

    return BuiltModel(
        model=model,
        x=x,
        matchups=matchups,
        slots=slots,
        team_matchups=team_matchups,
        slot_overlaps=slot_overlaps,
        divisions_by_id=divisions_by_id,
        fields_by_id=fields_by_id,
        teams_by_id=teams_by_id,
        assumption_lits=list(reg.lits),
        assumptions=dict(reg.assumptions),
    )


def solve(spec: TournamentSpec, pools: list[Pool]) -> TournamentSchedule:
    """Build and solve the CP-SAT model for all pool-play games.

    This is the main entry point.  It:
    1. Builds the hard-constraint model via `build_model` (fast path).
    2. Adds weighted soft-constraint penalties and an objective.
    3. Solves and extracts the schedule.
    """
    built = build_model(spec, pools, instrument=False)

    if not built.matchups:
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

    model = built.model
    x = built.x
    matchups = built.matchups
    slots = built.slots
    team_matchups = built.team_matchups
    divisions_by_id = built.divisions_by_id
    teams_by_id = built.teams_by_id

    # -- Soft constraints ------------------------------------------------------
    penalties: list[cp_model.LinearExpr] = []

    # S1. Minimize back-to-back games (high weight).
    back_to_back_weight = 10
    for team_id, m_indices in team_matchups.items():
        if len(m_indices) < 2:
            continue
        division_id = teams_by_id[team_id].division_id
        division = divisions_by_id[division_id]
        half_rest = (
            timedelta(minutes=division.min_rest_minutes // 2)
            if division.min_rest_minutes > 0
            else timedelta(minutes=spec.total_game_minutes(division))
        )

        for m1, m2 in itertools.combinations(m_indices, 2):
            for s1 in [si for (mi, si) in x if mi == m1]:
                for s2 in [si for (mi, si) in x if mi == m2]:
                    if _slots_close_but_feasible(slots[s1], slots[s2], half_rest, division, spec):
                        pen = model.new_bool_var(f"btb_{team_id}_{m1}_{m2}_{s1}_{s2}")
                        model.add(x[m1, s1] + x[m2, s2] - 1 <= pen)
                        penalties.append(back_to_back_weight * pen)

    # S2. Balance early/late slots across teams (medium weight).
    early_late_weight = 3
    early_cutoff_hour = 9
    late_cutoff_hour = 17

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
        if pref.target_type == "division":
            target_team_ids = [t.id for t in spec.teams_in_division(pref.target)]
        else:
            target_team_ids = [pref.target]

        for tid in target_team_ids:
            for m_idx in team_matchups.get(tid, []):
                for s_idx in [si for (mi, si) in x if mi == m_idx]:
                    slot = slots[s_idx]
                    if not _in_any_window(slot["start"], slot["end"], pref.preferred_windows):
                        pen = model.new_bool_var(f"timepref_{tid}_{m_idx}_{s_idx}")
                        model.add(x[m_idx, s_idx] <= pen)
                        penalties.append(weight * pen)

    if penalties:
        model.minimize(sum(penalties))

    # -- Solve -----------------------------------------------------------------
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

        rr_games = list(itertools.combinations(teams, 2))

        games_per_team = division.games_per_team
        if games_per_team >= n - 1:
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

    padded = list(teams)
    if n % 2 == 1:
        padded.append("__BYE__")

    k = len(padded)
    rounds: list[list[tuple[str, str]]] = []

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
        rotating = [rotating[-1]] + rotating[:-1]

    selected: list[tuple[str, str]] = []
    for round_games in rounds:
        if len(selected) > 0:
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

    for fld in spec.fields:
        compatible_divisions = divisions_by_size.get(fld.size.value, [])
        if not compatible_divisions:
            continue

        slot_minutes = max(spec.total_game_minutes(d) for d in compatible_divisions)

        for window in fld.availability:
            current = window.start
            while current + timedelta(minutes=slot_minutes) <= window.end:
                slots.append(
                    {
                        "field_id": fld.id,
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

    gap_1_to_2 = (slot2["start"] - end1).total_seconds()
    gap_2_to_1 = (slot1["start"] - end2).total_seconds()

    min_rest_secs = min_rest.total_seconds()

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

    return (0 <= gap_1_to_2 < threshold_secs) or (0 <= gap_2_to_1 < threshold_secs)


def _in_any_window(start: datetime, end: datetime, windows: list) -> bool:
    """Check if a time range falls within any of the given windows."""
    for w in windows:
        if start >= w.start and end <= w.end:
            return True
    return False
