"""Tests for tourneydesk.explain -- NL conflict explanation + repair options (M5).

Entirely offline: no network calls, no `ANTHROPIC_API_KEY` required. The LLM
path itself is only exercised indirectly (its post-validation guard is tested
directly with a fabricated payload); it must remain import-safe without a key.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tournament_scheduler.conflict import ConflictItem, ConflictSet, extract_conflict
from tournament_scheduler.models import DivisionSpec, FieldSize, FieldSpec, TeamSpec, TimeWindow, TournamentSpec
from tournament_scheduler.pools import assign_pools
from tournament_scheduler.spec_io import load_spec
from tourneydesk.explain.engine import (
    _UngroundedExplanationError,
    _validate_grounding,
    explain_conflict,
    explain_infeasible_spec,
)
from tourneydesk.explain.models import ConflictExplanation, RepairOption, SpecEdit

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIEFS_DIR = REPO_ROOT / "evals" / "briefs"
EXAMPLES_DIR = REPO_ROOT / "examples"
DAY = datetime(2026, 9, 12)


def _all_spec_ids(spec: TournamentSpec) -> set[str]:
    return {d.id for d in spec.divisions} | {f.id for f in spec.fields} | {t.id for t in spec.teams}


def _assert_well_formed(explanation: ConflictExplanation, conflict: ConflictSet, spec: TournamentSpec) -> None:
    assert explanation is not None
    headline = explanation.headline.lower()
    assert "cannot" in headline or "infeasible" in headline or "can't" in headline

    descriptors = {item.descriptor for item in conflict.involves}
    assert explanation.grounding
    assert set(explanation.grounding) <= descriptors

    assert 2 <= len(explanation.repairs) <= 3
    real_ids = _all_spec_ids(spec)
    for repair in explanation.repairs:
        assert repair.title
        assert repair.description
        assert repair.tradeoff
        assert repair.spec_edits
        assert any(edit.target_id in real_ids for edit in repair.spec_edits)


# ---------------------------------------------------------------------------
# Golden infeasible specs
# ---------------------------------------------------------------------------


class TestGoldenInfeasibleSpecs:
    @pytest.mark.parametrize("brief_id", ["b13_infeasible_rest", "b14_infeasible_fields"])
    def test_deterministic_explanation(self, brief_id: str) -> None:
        spec = load_spec(BRIEFS_DIR / brief_id / "golden_spec.yaml")

        explanation = explain_infeasible_spec(spec, use_llm=False)
        assert explanation is not None

        conflict = extract_conflict(spec, assign_pools(spec), time_limit_s=10.0)
        assert conflict is not None
        _assert_well_formed(explanation, conflict, spec)


# ---------------------------------------------------------------------------
# Family-specific repair logic (tiny in-code infeasible specs)
# ---------------------------------------------------------------------------


def _rest_conflict_spec() -> TournamentSpec:
    """4 teams, 1 field, 2h window, 120min rest -- rest is the binding constraint."""
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


def _capacity_conflict_spec() -> TournamentSpec:
    """4 teams (6 games), 1 field, only 3 slots -- field capacity is the binding constraint."""
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


class TestFamilySpecificRepairs:
    def test_rest_repair_updates_min_rest(self) -> None:
        spec = _rest_conflict_spec()
        conflict = extract_conflict(spec, assign_pools(spec), time_limit_s=10.0)
        assert conflict is not None
        assert "rest" in conflict.groups()

        explanation = explain_conflict(spec, conflict, use_llm=False)
        rest_edits = [
            edit
            for repair in explanation.repairs
            for edit in repair.spec_edits
            if edit.op == "update_division" and edit.field == "min_rest_minutes"
        ]
        assert rest_edits, "expected an update_division repair targeting min_rest_minutes"
        assert rest_edits[0].target_id == "u12"

    def test_capacity_repair_extends_field_or_reduces_games(self) -> None:
        spec = _capacity_conflict_spec()
        conflict = extract_conflict(spec, assign_pools(spec), time_limit_s=10.0)
        assert conflict is not None
        assert "field_double_booking" in conflict.groups() or "assignment" in conflict.groups()

        explanation = explain_conflict(spec, conflict, use_llm=False)
        ops = {(edit.op, edit.field) for repair in explanation.repairs for edit in repair.spec_edits}
        assert ("set_field_availability", "availability") in ops or ("update_division", "games_per_team") in ops


# ---------------------------------------------------------------------------
# Feasible spec -> None
# ---------------------------------------------------------------------------


class TestFeasibleSpecReturnsNone:
    def test_small_tournament_example(self) -> None:
        spec = load_spec(EXAMPLES_DIR / "small_tournament.yaml")
        assert explain_infeasible_spec(spec, use_llm=False) is None


# ---------------------------------------------------------------------------
# Grounding guard (anti-confabulation) -- unit test on the validator directly
# ---------------------------------------------------------------------------


class TestGroundingGuard:
    def test_rejects_fabricated_ungrounded_output(self) -> None:
        conflict = ConflictSet(
            summary="The tournament cannot be scheduled: minimum rest between a team's games cannot be satisfied.",
            involves=[
                ConflictItem(
                    group="rest",
                    descriptor="Minimum rest 120min for team Team 1",
                    spec_ids=["t1", "u12"],
                )
            ],
            minimal=True,
            core_size=1,
        )
        fabricated = ConflictExplanation(
            headline="This tournament cannot be scheduled.",
            narrative="A narrative that invents a fact not present in the conflict.",
            repairs=[
                RepairOption(
                    title="Do X",
                    description="Description.",
                    tradeoff="Tradeoff.",
                    spec_edits=[SpecEdit(op="other", target_id="u12", field=None, new_value=None, note="note")],
                ),
                RepairOption(
                    title="Do Y",
                    description="Description.",
                    tradeoff="Tradeoff.",
                    spec_edits=[SpecEdit(op="other", target_id="u12", field=None, new_value=None, note="note")],
                ),
            ],
            # Not a verbatim descriptor from `conflict.involves` -- must be rejected.
            grounding=["Field 7 is double-booked on Mars"],
        )

        with pytest.raises(_UngroundedExplanationError):
            _validate_grounding(fabricated, conflict)


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


class TestCliSmoke:
    def test_infeasible_json_exit_code(self) -> None:
        spec_path = BRIEFS_DIR / "b13_infeasible_rest" / "golden_spec.yaml"
        result = subprocess.run(
            [sys.executable, "-m", "tourneydesk.explain", str(spec_path), "--no-llm", "--json"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=30,
        )
        assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        data = json.loads(result.stdout)
        assert "headline" in data
        assert "repairs" in data
        assert len(data["repairs"]) >= 2
