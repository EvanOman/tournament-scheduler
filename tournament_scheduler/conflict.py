"""Infeasibility engine: minimal conflict extraction (M5).

When a `TournamentSpec` cannot be scheduled, this module explains *why* by
computing a small, human-readable set of hard-constraint groups that are
jointly unsatisfiable.

Approach (see DESIGN.md §6)
---------------------------
1. Build the instrumented model (`solver.build_model(..., instrument=True)`),
   which guards every hard-constraint group behind a CP-SAT assumption literal.
2. Assert every group literal true (`model.add_assumptions([...])`) and solve.
   If the result is not INFEASIBLE, the spec is schedulable and we return None.
3. If INFEASIBLE, take `solver.sufficient_assumptions_for_infeasibility()` as an
   initial unsat core, then shrink it toward a *minimal* unsatisfiable subset
   (MUS) with the **deletion filter** — the standard destructive-MUS algorithm:
   try dropping each assumption in turn; re-solve with the reduced set; keep the
   drop only if the model is still provably INFEASIBLE, otherwise the dropped
   group is essential and stays.  (van Loon 1981; Chinneck 2008,
   *Feasibility and Infeasibility in Optimization*; Marques-Silva & Lynce 2011,
   "On Improving MUS Extraction Algorithms".)

The whole procedure is time-boxed; if the budget is exhausted before the core
is proven minimal we return the best (possibly non-minimal) core with
``minimal=False`` — an acceptable fallback per the design.
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model
from pydantic import BaseModel

from tournament_scheduler.models import Pool, TournamentSpec
from tournament_scheduler.solver import GroupDescriptor, build_model

# Plain-English lead-in per constraint family, used to compose the summary.
_FAMILY_PHRASING: dict[str, str] = {
    "assignment": "every required game must be scheduled",
    "availability": "a matchup has no field/time it can be placed on",
    "field_double_booking": "field capacity (one game per slot)",
    "team_simultaneous": "no team can play two games at once",
    "rest": "minimum rest between a team's games",
    "coaching": "a shared coach cannot be in two places at once",
}


class ConflictItem(BaseModel):
    """One hard-constraint group participating in an infeasibility."""

    group: str  # constraint family, e.g. "rest"
    descriptor: str  # human-readable, e.g. "Minimum rest 90min for team u14b_team_03"
    spec_ids: list[str]  # relevant spec object ids (team/field/division/coach)


class ConflictSet(BaseModel):
    """A minimal (or best-effort) set of constraint groups that cannot co-exist."""

    summary: str  # plain-English one-liner
    involves: list[ConflictItem]
    minimal: bool  # True iff proven minimal (no group removable) within the time budget
    core_size: int

    def groups(self) -> set[str]:
        """Set of constraint-family names involved in the conflict."""
        return {item.group for item in self.involves}

    def describe(self) -> str:
        """Multi-line plain-English explanation suitable for an NL layer / tests."""
        lines = [self.summary, "", "Conflicting requirements:"]
        for item in self.involves:
            lines.append(f"  - {item.descriptor}")
        if not self.minimal:
            lines.append("")
            lines.append("(Note: this conflict set may not be fully minimal — the analysis was time-boxed.)")
        return "\n".join(lines)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


def extract_conflict(
    spec: TournamentSpec,
    pools: list[Pool],
    *,
    time_limit_s: float = 10.0,
) -> ConflictSet | None:
    """Return a minimal conflict set explaining why ``spec`` is infeasible.

    Returns ``None`` if the spec is actually schedulable (feasible).  The whole
    procedure — initial solve plus the deletion-filter shrink loop — is bounded
    by ``time_limit_s``; on timeout a non-minimal core is returned with
    ``minimal=False``.
    """
    deadline = time.monotonic() + time_limit_s

    built = build_model(spec, pools, instrument=True)
    model = built.model
    descriptors: dict[int, GroupDescriptor] = built.assumptions
    all_lits = list(built.assumption_lits)

    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1  # deterministic, single-threaded core extraction
    solver.parameters.log_search_progress = False

    def solve_with(lits: list[cp_model.IntVar], remaining: float):
        model.clear_assumptions()
        model.add_assumptions(lits)
        solver.parameters.max_time_in_seconds = max(0.01, remaining)
        return solver.solve(model)

    # -- Initial full solve ----------------------------------------------------
    status = solve_with(all_lits, time_limit_s)
    if status != cp_model.INFEASIBLE:
        # Feasible (OPTIMAL/FEASIBLE) or undetermined (UNKNOWN) -> not a proven conflict.
        return None

    index_to_lit = {lit.index: lit for lit in all_lits}
    core_indices = solver.sufficient_assumptions_for_infeasibility()
    core: list[cp_model.IntVar] = [index_to_lit[i] for i in core_indices if i in index_to_lit]
    if not core:
        # Degenerate: infeasible even with no assumptions asserted. Fall back to
        # the full set so we still report something meaningful.
        core = list(all_lits)

    # -- Deletion-filter shrink toward a minimal unsat core --------------------
    minimal = True
    i = 0
    while i < len(core):
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            minimal = False  # ran out of budget before proving minimality
            break
        candidate = core[:i] + core[i + 1 :]
        if not candidate:
            # Cannot drop the last remaining assumption.
            i += 1
            continue
        st = solve_with(candidate, remaining)
        if st == cp_model.INFEASIBLE:
            core = candidate  # dropped group was not essential
        else:
            i += 1  # essential (or unproven) -> keep it, advance
            if st != cp_model.OPTIMAL and st != cp_model.FEASIBLE:
                # UNKNOWN: we could not prove removability, so not certified minimal.
                minimal = False

    items = _build_items(core, descriptors)
    return ConflictSet(
        summary=_summarize(items),
        involves=items,
        minimal=minimal,
        core_size=len(items),
    )


def _build_items(
    core: list[cp_model.IntVar],
    descriptors: dict[int, GroupDescriptor],
) -> list[ConflictItem]:
    items = [
        ConflictItem(group=d.group, descriptor=d.descriptor, spec_ids=list(d.spec_ids))
        for lit in core
        if (d := descriptors.get(lit.index)) is not None
    ]
    # Deterministic ordering for stable summaries and golden assertions.
    items.sort(key=lambda it: (it.group, it.descriptor))
    return items


def _summarize(items: list[ConflictItem]) -> str:
    """Compose a plain-English one-liner from the families involved."""
    if not items:
        return "The tournament could not be scheduled, but no specific conflict was isolated."

    families = list(dict.fromkeys(item.group for item in items))  # unique, order-preserving
    phrases = [_FAMILY_PHRASING.get(fam, fam) for fam in families]

    if len(phrases) == 1:
        clause = phrases[0]
    elif len(phrases) == 2:
        clause = f"{phrases[0]} and {phrases[1]}"
    else:
        clause = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"

    return f"The tournament cannot be scheduled: {clause} cannot all be satisfied together."
