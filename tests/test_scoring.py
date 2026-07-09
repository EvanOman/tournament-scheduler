"""Deterministic tests for evals/scoring.py -- no network, no LLM calls.

Covers the hand-built spec-pair cases from the M2 eval-runner spec plus a
corpus-wide self-check: `score_spec(golden, golden)` must be a perfect score
for every brief's golden_spec.yaml. That self-check doubles as a CI gate
against scorer/corpus format drift.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from evals.scoring import score_spec
from tournament_scheduler.models import (
    CoachingConflict,
    ConstraintPriority,
    DivisionSpec,
    FieldSize,
    FieldSpec,
    TeamSpec,
    TimePreference,
    TimeWindow,
    TournamentSpec,
)
from tournament_scheduler.spec_io import load_spec

BRIEFS_DIR = Path(__file__).resolve().parent.parent / "evals" / "briefs"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _division(id_: str = "u10", **overrides: object) -> DivisionSpec:
    kwargs: dict[str, object] = {
        "id": id_,
        "name": "U10 Boys",
        "field_size": FieldSize.MEDIUM,
        "game_duration_minutes": 25,
        "halftime_minutes": 5,
        "buffer_minutes": 10,
        "min_rest_minutes": 45,
        "games_per_team": 3,
        "pool_size": 4,
        "bracket_after_pools": True,
    }
    kwargs.update(overrides)
    return DivisionSpec(**kwargs)


def _team(id_: str, name: str, division_id: str = "u10") -> TeamSpec:
    return TeamSpec(id=id_, name=name, division_id=division_id)


def _field(id_: str = "f1", name: str = "Field 1", **overrides: object) -> FieldSpec:
    kwargs: dict[str, object] = {
        "id": id_,
        "name": name,
        "size": FieldSize.MEDIUM,
        "availability": [TimeWindow(start=datetime(2026, 9, 12, 8, 0), end=datetime(2026, 9, 12, 18, 0))],
    }
    kwargs.update(overrides)
    return FieldSpec(**kwargs)


_DEFAULT_TEAM_NAMES = ["Alpha FC", "Beta SC", "Gamma FC", "Delta SC"]


def _base_spec(**overrides: object) -> TournamentSpec:
    kwargs: dict[str, object] = {
        "name": "Test Cup",
        "divisions": [_division()],
        "teams": [_team(f"u10_t{i}", name) for i, name in enumerate(_DEFAULT_TEAM_NAMES, start=1)],
        "fields": [_field()],
    }
    kwargs.update(overrides)
    return TournamentSpec(**kwargs)


# ---------------------------------------------------------------------------
# Hand-built pair cases
# ---------------------------------------------------------------------------


def test_identical_specs_score_perfect():
    spec = _base_spec()
    score = score_spec(spec, spec)
    assert score.precision == 1.0
    assert score.recall == 1.0
    assert score.f1 == 1.0
    assert score.hallucinated_count == 0
    for category in score.category_scores():
        assert category.missing == []
        assert category.extra == []


def test_missing_coaching_conflict_lowers_recall_with_description():
    golden = _base_spec(
        coaching_conflicts=[CoachingConflict(coach_name="Coach Ramirez", team_ids=["u10_t1", "u10_t2"])]
    )
    final = _base_spec()  # same teams/fields, coaching conflict never recorded

    score = score_spec(final, golden)

    assert score.coaching_conflicts.matched == 0
    assert score.coaching_conflicts.missing == [
        "coaching conflict for 'Coach Ramirez' (teams: ['alpha fc', 'beta sc']) is missing."
    ]
    assert score.recall < 1.0
    assert score.precision == 1.0
    assert score.hallucinated_count == 0


def test_extra_time_preference_lowers_precision_and_flags_hallucination():
    golden = _base_spec()
    final = _base_spec(
        time_preferences=[
            TimePreference(
                target="u10",
                target_type="division",
                preferred_windows=[TimeWindow(start=datetime(2026, 9, 12, 8, 0), end=datetime(2026, 9, 12, 10, 0))],
                priority=ConstraintPriority.MEDIUM,
            )
        ]
    )

    score = score_spec(final, golden)

    assert len(score.time_preferences.extra) == 1
    assert score.precision < 1.0
    assert score.recall == 1.0
    assert score.hallucinated_count == 1


def test_placeholder_team_names_use_count_only_comparison():
    golden = _base_spec(teams=[_team(f"u10_team_{i:02d}", f"Team {i}") for i in range(1, 5)])
    final = _base_spec(
        teams=[
            _team(f"u10_named_{i}", name) for i, name in enumerate(["Foo FC", "Bar SC", "Baz FC", "Qux SC"], start=1)
        ]
    )

    score = score_spec(final, golden)

    # Golden names match the placeholder pattern -> a single count-only item,
    # not a 4-miss/4-extra name-set mismatch.
    assert score.teams.matched == 1
    assert score.teams.missing == []
    assert score.teams.extra == []


def test_placeholder_team_count_mismatch_is_a_miss():
    golden = _base_spec(teams=[_team(f"u10_team_{i:02d}", f"Team {i}") for i in range(1, 5)])
    final = _base_spec(teams=[_team(f"u10_team_{i:02d}", f"Team {i}") for i in range(1, 4)])

    score = score_spec(final, golden)

    assert score.teams.matched == 0
    assert len(score.teams.missing) == 1
    assert "expected 4 additional team(s)" in score.teams.missing[0]


def test_division_param_mismatch_is_a_division_category_miss():
    golden = _base_spec()
    final = _base_spec(divisions=[_division(game_duration_minutes=99)])

    score = score_spec(final, golden)

    assert score.divisions.matched == 0
    assert len(score.divisions.missing) == 1
    assert "game_duration_minutes" in score.divisions.missing[0]
    assert score.recall < 1.0


# ---------------------------------------------------------------------------
# Corpus-wide golden self-check (CI gate against scorer/corpus format drift)
# ---------------------------------------------------------------------------


def _discover_brief_dirs() -> list[Path]:
    if not BRIEFS_DIR.exists():
        return []
    return sorted(p.parent for p in BRIEFS_DIR.glob("*/brief.yaml"))


BRIEF_DIRS = _discover_brief_dirs()
BRIEF_IDS = [d.name for d in BRIEF_DIRS]


@pytest.mark.parametrize("brief_dir", BRIEF_DIRS, ids=BRIEF_IDS)
def test_golden_spec_self_check_is_perfect(brief_dir: Path):
    golden_path = brief_dir / "golden_spec.yaml"
    if not golden_path.exists():
        pytest.skip(f"{brief_dir.name}: no golden_spec.yaml")

    golden = load_spec(golden_path)
    score = score_spec(golden, golden)

    assert score.precision == 1.0, f"{brief_dir.name}: {score.model_dump_json(indent=2)}"
    assert score.recall == 1.0, f"{brief_dir.name}: {score.model_dump_json(indent=2)}"
    assert score.f1 == 1.0, f"{brief_dir.name}: {score.model_dump_json(indent=2)}"
    assert score.hallucinated_count == 0, f"{brief_dir.name}: {score.model_dump_json(indent=2)}"


@pytest.mark.parametrize("brief_dir", BRIEF_DIRS, ids=BRIEF_IDS)
def test_golden_self_check_with_facts_is_perfect(brief_dir: Path):
    """Self-check must also hold when team-name matching is scoped to the facts."""
    golden_path = brief_dir / "golden_spec.yaml"
    if not golden_path.exists():
        pytest.skip(f"{brief_dir.name}: no golden_spec.yaml")

    brief = yaml.safe_load((brief_dir / "brief.yaml").read_text())
    golden = load_spec(golden_path)
    score = score_spec(golden, golden, facts_text=brief.get("facts"))

    assert score.f1 == 1.0, f"{brief_dir.name}: {score.model_dump_json(indent=2)}"
    assert score.hallucinated_count == 0


def test_untraceable_golden_team_names_score_count_only():
    """Golden names the persona can never state must not tank the teams score.

    The golden spec names real clubs, but the facts never enumerate them --
    an agent that captured the correct COUNT via placeholders scores a match.
    """
    golden = _base_spec(
        teams=[
            _team("t1", "Coastal FC"),
            _team("t2", "Highland United"),
            _team("t3", "Riverside SC"),
            _team("t4", "Summit FC"),
        ]
    )
    final = _base_spec(
        teams=[
            _team("u10_team_1", "Team 1"),
            _team("u10_team_2", "Team 2"),
            _team("u10_team_3", "Team 3"),
            _team("u10_team_4", "Team 4"),
        ]
    )
    facts = "8 teams... er, 4 teams in the division. No names decided yet."
    score = score_spec(final, golden, facts_text=facts)
    assert score.teams.recall == 1.0, score.teams.model_dump_json(indent=2)
    assert score.teams.precision == 1.0

    # Wrong count still fails.
    short = _base_spec(teams=[_team("u10_team_1", "Team 1"), _team("u10_team_2", "Team 2")])
    score2 = score_spec(short, golden, facts_text=facts)
    assert score2.teams.recall < 1.0


def test_bracket_flag_not_scored_when_facts_are_silent():
    """bracket_after_pools only counts when the facts mention brackets/playoffs."""
    golden = _base_spec()  # bracket_after_pools=True (model default)
    final = _base_spec(divisions=[_division(bracket_after_pools=False)])

    silent_facts = "One division, four teams, 25 minute games on one field."
    assert score_spec(final, golden, facts_text=silent_facts).divisions.recall == 1.0

    stated_facts = "One division; top four go to a single-elimination bracket after pools."
    assert score_spec(final, golden, facts_text=stated_facts).divisions.recall < 1.0

    # No facts context at all -> strict comparison (self-check path unaffected).
    assert score_spec(final, golden).divisions.recall < 1.0


def test_divisions_match_by_name_when_agent_chose_different_id():
    """Agent-derived division ids must not tank scoring; name is the stable key."""
    golden = _base_spec()  # division id 'u10', name 'U10 Boys'
    final = _base_spec(
        divisions=[_division(id_="u10_boys")],
        teams=[_team(f"u10_boys_t{i}", name, "u10_boys") for i, name in enumerate(_DEFAULT_TEAM_NAMES, 1)],
    )
    score = score_spec(final, golden)
    assert score.divisions.recall == 1.0, score.divisions.model_dump_json(indent=2)
    assert score.divisions.precision == 1.0
    assert score.teams.recall == 1.0, score.teams.model_dump_json(indent=2)
