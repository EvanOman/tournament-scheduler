"""Tests for the M2 eval brief corpus (evals/briefs/<id>/).

Validates the shared brief.yaml contract (docs/DESIGN.md §5) and confirms
every golden_spec.yaml loads, and that feasible golden specs actually solve
to a valid schedule. Infeasible golden specs are only asserted to parse --
solving them is skipped to keep the suite fast (their infeasibility is a
design property, not something re-verified on every test run).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tournament_scheduler.pools import assign_pools
from tournament_scheduler.solver import solve
from tournament_scheduler.spec_io import load_spec
from tournament_scheduler.validator import validate

BRIEFS_DIR = Path(__file__).resolve().parent.parent / "evals" / "briefs"

VALID_DIFFICULTIES = {"easy", "medium", "hard", "adversarial"}
VALID_CATEGORIES = {
    "clean",
    "rambling",
    "contradictory",
    "missing_info",
    "reversal",
    "unit_confusion",
    "infeasible",
    "disruption",
}
REQUIRED_KEYS = {
    "id",
    "title",
    "difficulty",
    "categories",
    "persona",
    "facts",
    "golden_questions",
    "expect_infeasible",
    "golden_conflict",
}


def _discover_brief_dirs() -> list[Path]:
    if not BRIEFS_DIR.exists():
        return []
    return sorted(p.parent for p in BRIEFS_DIR.glob("*/brief.yaml"))


BRIEF_DIRS = _discover_brief_dirs()
BRIEF_IDS = [d.name for d in BRIEF_DIRS]


def test_corpus_has_fifteen_briefs():
    assert len(BRIEF_DIRS) == 15


@pytest.mark.parametrize("brief_dir", BRIEF_DIRS, ids=BRIEF_IDS)
class TestBriefContract:
    """Structural checks against the shared brief.yaml contract."""

    def _load(self, brief_dir: Path) -> dict:
        text = (brief_dir / "brief.yaml").read_text()
        data = yaml.safe_load(text)
        assert isinstance(data, dict), f"{brief_dir.name}: brief.yaml did not parse to a mapping"
        return data

    def test_parses_and_has_required_keys(self, brief_dir: Path):
        data = self._load(brief_dir)
        missing = REQUIRED_KEYS - data.keys()
        assert not missing, f"{brief_dir.name}: missing keys {missing}"

    def test_id_matches_directory(self, brief_dir: Path):
        data = self._load(brief_dir)
        assert data["id"] == brief_dir.name

    def test_difficulty_is_valid(self, brief_dir: Path):
        data = self._load(brief_dir)
        assert data["difficulty"] in VALID_DIFFICULTIES, f"{brief_dir.name}: invalid difficulty {data['difficulty']!r}"

    def test_categories_are_valid(self, brief_dir: Path):
        data = self._load(brief_dir)
        categories = data["categories"]
        assert isinstance(categories, list) and categories, f"{brief_dir.name}: categories must be a non-empty list"
        invalid = set(categories) - VALID_CATEGORIES
        assert not invalid, f"{brief_dir.name}: invalid categories {invalid}"

    def test_golden_questions_is_a_list(self, brief_dir: Path):
        data = self._load(brief_dir)
        assert isinstance(data["golden_questions"], list), f"{brief_dir.name}: golden_questions must be a list"
        for gq in data["golden_questions"]:
            assert isinstance(gq, dict) and "about" in gq and "note" in gq, (
                f"{brief_dir.name}: each golden_question needs 'about' and 'note'"
            )

    def test_infeasible_briefs_have_golden_conflict(self, brief_dir: Path):
        data = self._load(brief_dir)
        if data["expect_infeasible"]:
            conflict = data["golden_conflict"]
            assert isinstance(conflict, dict), (
                f"{brief_dir.name}: expect_infeasible=true requires a golden_conflict dict"
            )
            assert conflict.get("summary"), f"{brief_dir.name}: golden_conflict.summary must be non-empty"
            assert isinstance(conflict.get("involves"), list) and conflict["involves"], (
                f"{brief_dir.name}: golden_conflict.involves must be a non-empty list"
            )
        else:
            assert data["golden_conflict"] is None, f"{brief_dir.name}: feasible briefs must have golden_conflict: null"


@pytest.mark.parametrize("brief_dir", BRIEF_DIRS, ids=BRIEF_IDS)
def test_golden_spec_parses(brief_dir: Path):
    """Every brief with a golden_spec.yaml must load as a valid TournamentSpec."""
    spec_path = brief_dir / "golden_spec.yaml"
    if not spec_path.exists():
        pytest.skip(f"{brief_dir.name}: no golden_spec.yaml (infeasible brief with spec omitted)")
    load_spec(spec_path)  # raises on failure


@pytest.mark.parametrize("brief_dir", BRIEF_DIRS, ids=BRIEF_IDS)
def test_feasible_golden_spec_solves_and_validates(brief_dir: Path):
    """Feasible golden specs must assign_pools -> solve -> validate to a VALID schedule."""
    brief_data = yaml.safe_load((brief_dir / "brief.yaml").read_text())
    spec_path = brief_dir / "golden_spec.yaml"

    if brief_data["expect_infeasible"]:
        pytest.skip(f"{brief_dir.name}: infeasible brief, not solved here (parse-only, see test_golden_spec_parses)")

    assert spec_path.exists(), f"{brief_dir.name}: feasible brief must ship a golden_spec.yaml"

    spec = load_spec(spec_path)
    pools = assign_pools(spec)
    schedule = solve(spec, pools)
    result = validate(schedule, spec)

    assert schedule.stats.status in ("OPTIMAL", "FEASIBLE"), (
        f"{brief_dir.name}: solver status {schedule.stats.status}, expected OPTIMAL/FEASIBLE"
    )
    assert result.valid, f"{brief_dir.name}: schedule failed validation:\n{result.summary()}"
