"""Deterministic tests for evals/runner.py -- no network, no LLM calls.

Exercises the `--provider fake` path only. `--provider claude` makes live
Anthropic API calls and is intentionally never invoked here -- that is the
orchestrator's post-merge live-smoke job.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from evals.runner import descriptor_to_family, golden_conflict_families, run_brief, run_corpus
from tests.test_fake_e2e import DIRECTOR_LINES, SCRIPT
from tournament_scheduler.conflict import extract_conflict
from tournament_scheduler.pools import assign_pools
from tournament_scheduler.spec_io import load_spec, save_spec
from tourneydesk.core import IntakeService
from tourneydesk.persona import FakePersona
from tourneydesk.providers.fake import FakeIntake
from tourneydesk.session import SpecSession

BRIEFS_DIR = Path(__file__).resolve().parent.parent / "evals" / "briefs"


# ---------------------------------------------------------------------------
# Conflict-family mapping
# ---------------------------------------------------------------------------


def test_descriptor_to_family_mapping():
    assert descriptor_to_family("division:u12 min_rest_minutes=180") == "rest"
    assert descriptor_to_family("Coach Alvarez cannot be double-booked") == "coaching"
    assert descriptor_to_family("teams: 16 registered in u12") == "assignment"
    assert descriptor_to_family("field:f1 availability 08:00-14:00 (only field in the tournament)") == "assignment"


def test_golden_conflict_families_dedupes_and_preserves_order():
    involves = [
        "division:u12 min_rest_minutes=180",
        "division:u12 games_per_team=3",
        "field:f1 availability 08:00-14:00",
    ]
    assert golden_conflict_families(involves) == ["rest", "assignment"]


@pytest.mark.parametrize("brief_id", ["b13_infeasible_rest", "b14_infeasible_fields"])
def test_infeasible_golden_specs_conflict_families_cover_golden_conflict(brief_id: str):
    """DESIGN.md sec 4 gate: infeasible fixtures must produce a minimal conflict
    set whose families are consistent with the brief's golden_conflict."""
    brief_dir = BRIEFS_DIR / brief_id
    brief = yaml.safe_load((brief_dir / "brief.yaml").read_text())
    golden = load_spec(brief_dir / "golden_spec.yaml")
    pools = assign_pools(golden)

    conflict = extract_conflict(golden, pools)

    assert conflict is not None, f"{brief_id}: golden_spec.yaml is expected to be infeasible"
    mapped = golden_conflict_families(brief["golden_conflict"]["involves"])
    assert set(mapped).issubset(conflict.groups()), (
        f"{brief_id}: mapped families {mapped} not covered by extracted groups {sorted(conflict.groups())}"
    )


# ---------------------------------------------------------------------------
# run_brief / run_corpus plumbing (FakeIntake path)
# ---------------------------------------------------------------------------


def _write_fake_brief(brief_dir: Path) -> None:
    brief_dir.mkdir(parents=True)
    data = {
        "id": brief_dir.name,
        "title": "Fake plumbing test",
        "difficulty": "easy",
        "categories": ["clean"],
        "persona": "n/a -- fake provider path, no persona LLM call is made",
        "facts": "n/a",
        "golden_questions": [],
        "expect_infeasible": False,
        "golden_conflict": None,
        "script": SCRIPT,
        "messages": DIRECTOR_LINES,
    }
    (brief_dir / "brief.yaml").write_text(yaml.dump(data, sort_keys=False))


def _materialize_fake_spec():
    session = SpecSession()
    provider = FakeIntake(session, SCRIPT)
    persona = FakePersona(DIRECTOR_LINES)
    service = IntakeService(provider)
    asyncio.run(service.run_conversation(persona, max_turns=20))
    spec, _assumptions = session.to_spec()
    return spec


def test_run_brief_skips_fake_provider_when_brief_has_no_script():
    # b01 is a real corpus brief with no script/messages -- fake mode must
    # skip cleanly rather than raise (per the deliverable spec).
    result = run_brief(BRIEFS_DIR / "b01_clean_small", provider_name="fake", max_turns=5)

    assert result.skipped is True
    assert result.skip_reason
    assert result.score is None


def test_run_brief_fake_provider_end_to_end(tmp_path: Path):
    brief_dir = tmp_path / "b_fake_plumbing"
    _write_fake_brief(brief_dir)
    save_spec(_materialize_fake_spec(), brief_dir / "golden_spec.yaml")

    result = run_brief(brief_dir, provider_name="fake", max_turns=20)

    assert result.skipped is False
    assert result.intake_complete is True
    assert result.spec_materialized is True
    assert result.turns_used > 0
    assert result.score is not None
    assert result.score.f1 == 1.0
    assert result.score.hallucinated_count == 0


def test_run_corpus_fake_provider_writes_results_json(tmp_path: Path):
    briefs_dir = tmp_path / "briefs"
    brief_dir = briefs_dir / "b_fake_plumbing"
    _write_fake_brief(brief_dir)
    save_spec(_materialize_fake_spec(), brief_dir / "golden_spec.yaml")

    out_dir = tmp_path / "results"
    corpus = run_corpus(briefs_dir, ids=None, provider_name="fake", out_path=out_dir, max_turns=20)

    assert corpus.aggregate.num_briefs == 1
    assert corpus.aggregate.num_scored == 1
    assert corpus.aggregate.num_skipped == 0
    assert corpus.aggregate.mean_f1 == 1.0
    assert corpus.provider == "fake"

    written = list(out_dir.glob("*.json"))
    assert len(written) == 1
    payload = written[0].read_text()
    assert '"brief_id": "b_fake_plumbing"' in payload


def test_run_corpus_respects_ids_filter(tmp_path: Path):
    briefs_dir = tmp_path / "briefs"
    for name in ("b_fake_one", "b_fake_two"):
        _write_fake_brief(briefs_dir / name)
    (briefs_dir / "b_fake_one" / "brief.yaml").write_text(
        yaml.dump({**yaml.safe_load((briefs_dir / "b_fake_one" / "brief.yaml").read_text()), "id": "b_fake_one"})
    )

    out_dir = tmp_path / "results"
    corpus = run_corpus(briefs_dir, ids=["b_fake_one"], provider_name="fake", out_path=out_dir, max_turns=20)

    assert corpus.aggregate.num_briefs == 1
    assert corpus.briefs[0].brief_id == "b_fake_one"
