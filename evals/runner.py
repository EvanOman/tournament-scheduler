"""Persona-driven eval runner (DESIGN.md sec 5).

Drives each brief's `ClaudePersona` against `ClaudeIntake` through
`IntakeService.run_conversation`, materializes the resulting `SpecSession`,
scores it against `golden_spec.yaml` with `evals.scoring.score_spec`, and (for
`expect_infeasible` briefs) cross-checks the extracted `ConflictSet` against
the brief's `golden_conflict`.

This module makes network calls when run with `--provider claude` (the
default) -- it is exercised by unit tests only via `--provider fake`, which
never touches the network. Do not run `--provider claude` in CI; that is the
orchestrator's live-smoke job, run after merge.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
import yaml
from pydantic import BaseModel, Field

import tourneydesk.prompts as prompts_module
from evals.scoring import SpecScore, score_spec
from tournament_scheduler.conflict import ConflictSet, extract_conflict
from tournament_scheduler.pools import assign_pools
from tournament_scheduler.spec_io import load_spec
from tourneydesk.core import IntakeService
from tourneydesk.persona import ClaudePersona, FakePersona
from tourneydesk.providers.base import Persona
from tourneydesk.providers.claude import DEFAULT_MODEL, ClaudeIntake
from tourneydesk.providers.fake import FakeIntake
from tourneydesk.session import IncompleteSpecError, SpecSession

# ---------------------------------------------------------------------------
# Conflict-family mapping (loose, documented heuristic)
# ---------------------------------------------------------------------------
#
# `golden_conflict.involves` descriptors (DESIGN.md sec 5) are spec-level
# facts ("division:u12 min_rest_minutes=180", "teams: 16 registered in u12").
# `ConflictSet.groups()` (tournament_scheduler/conflict.py) reports
# solver-internal constraint *families* (assignment, availability,
# field_double_booking, team_simultaneous, rest, coaching). The two vocabularies
# don't line up 1:1: e.g. a field-capacity descriptor like "field:f1
# availability 08:00-14:00" empirically surfaces in the solver's minimal core
# as the "assignment" family (too few valid (matchup, slot) pairings), not as
# "availability" (that family only fires when a specific matchup has *zero*
# feasible slots at all -- a different, rarer case; field availability windows
# are otherwise baked into decision-variable domains, not a separate
# assumption-guarded group). Verified empirically against both b13
# (min_rest_minutes=180 + a 6-hour window) and b14 (16 teams needing 24 games
# in one field's 2-hour window): both extract to {"assignment", "rest"} even
# though b14's golden_conflict never mentions rest, and b13's never uses the
# words "assignment" or "field_double_booking".
#
# Mapping rule (checked in order, first match wins):
#   1. descriptor mentions rest ("min_rest_minutes") -> "rest"
#   2. descriptor mentions a coach -> "coaching"
#   3. everything else (games_per_team, pool_size, team counts, field
#      availability windows) -> "assignment" (capacity/count pressure)
#
# The gate this feeds is a *subset* check (every mapped family must appear
# in the extracted groups), so mapping something to "assignment" is safe even
# when the true bottleneck also involves "field_double_booking" or
# "team_simultaneous" -- those just aren't required to be named in the brief.


def descriptor_to_family(descriptor: str) -> str:
    """Map one `golden_conflict.involves` descriptor to a solver constraint family."""
    lowered = descriptor.lower()
    if "min_rest_minutes" in lowered or "rest" in lowered:
        return "rest"
    if "coach" in lowered:
        return "coaching"
    return "assignment"


def golden_conflict_families(involves: list[str]) -> list[str]:
    """Unique, order-preserving mapped families for a brief's golden_conflict.involves."""
    seen: list[str] = []
    for descriptor in involves:
        family = descriptor_to_family(descriptor)
        if family not in seen:
            seen.append(family)
    return seen


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class BriefResult(BaseModel):
    """Outcome of running one brief through the persona <-> agent conversation."""

    brief_id: str
    title: str
    difficulty: str
    categories: list[str]
    provider: str
    expect_infeasible: bool

    skipped: bool = False
    skip_reason: str | None = None

    max_turns: int
    turns_used: int = 0
    wall_time_seconds: float = 0.0

    input_tokens: int | None = None
    output_tokens: int | None = None

    intake_complete: bool = False
    spec_materialized: bool = False
    missing_facts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    score: SpecScore | None = None

    conflict_extracted: bool | None = None
    conflict_summary: str | None = None
    conflict_families: list[str] = Field(default_factory=list)
    golden_conflict_families: list[str] = Field(default_factory=list)
    conflict_families_match: bool | None = None

    error: str | None = None


class CorpusAggregate(BaseModel):
    num_briefs: int
    num_scored: int
    num_skipped: int
    num_incomplete: int
    mean_precision: float
    mean_recall: float
    mean_f1: float
    total_hallucinated: int
    infeasible_briefs: int
    infeasible_conflict_family_matches: int


class CorpusResult(BaseModel):
    generated_at: str
    model: str
    prompt_version: str
    provider: str
    briefs: list[BriefResult]
    aggregate: CorpusAggregate


# ---------------------------------------------------------------------------
# Provider/persona construction
# ---------------------------------------------------------------------------


def _build_provider_and_persona(
    brief: dict[str, Any], provider_name: str, session: SpecSession
) -> tuple[Any, Persona] | tuple[None, None]:
    if provider_name == "claude":
        persona_text = f"{brief.get('persona', '')}\n\n{brief.get('facts', '')}".strip()
        return ClaudeIntake(session), ClaudePersona(persona_text)

    if provider_name == "fake":
        script = brief.get("script")
        messages = brief.get("messages")
        if not script or not messages:
            return None, None
        return FakeIntake(session, script), FakePersona(messages)

    raise ValueError(f"Unknown provider '{provider_name}'")


# ---------------------------------------------------------------------------
# run_brief
# ---------------------------------------------------------------------------


def run_brief(brief_dir: Path, provider_name: str = "claude", max_turns: int = 24) -> BriefResult:
    """Run one brief's persona <-> agent conversation and score the result.

    Never raises for expected failure modes (incomplete intake, fake-provider
    briefs with no script/messages) -- those come back as a `BriefResult`
    with `skipped=True` or `spec_materialized=False` so a corpus run can
    complete even when one brief fails.
    """
    return asyncio.run(_run_brief_async(brief_dir, provider_name, max_turns))


async def _run_brief_async(brief_dir: Path, provider_name: str, max_turns: int) -> BriefResult:
    brief = yaml.safe_load((brief_dir / "brief.yaml").read_text()) or {}
    common: dict[str, Any] = {
        "brief_id": brief["id"],
        "title": brief["title"],
        "difficulty": brief["difficulty"],
        "categories": brief["categories"],
        "provider": provider_name,
        "expect_infeasible": bool(brief["expect_infeasible"]),
        "max_turns": max_turns,
    }

    session = SpecSession()
    provider, persona = _build_provider_and_persona(brief, provider_name, session)
    if provider is None or persona is None:
        return BriefResult(
            **common,
            skipped=True,
            skip_reason=(
                f"--provider {provider_name} requires brief.yaml to include 'script' and 'messages' "
                "(FakeIntake path); this brief has neither."
            ),
        )

    service = IntakeService(provider)
    turns_used = 0

    def _count_turn(_director_message: str, _turn: object) -> None:
        nonlocal turns_used
        turns_used += 1

    start = time.monotonic()
    await service.run_conversation(persona, max_turns=max_turns, on_turn=_count_turn)
    wall_time = time.monotonic() - start

    input_tokens = getattr(provider, "total_input_tokens", None)
    output_tokens = getattr(provider, "total_output_tokens", None)

    try:
        final_spec, assumptions = session.to_spec()
    except IncompleteSpecError as exc:
        return BriefResult(
            **common,
            turns_used=turns_used,
            wall_time_seconds=wall_time,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            intake_complete=session.intake_complete,
            spec_materialized=False,
            missing_facts=list(exc.missing),
        )

    result_kwargs: dict[str, Any] = dict(
        **common,
        turns_used=turns_used,
        wall_time_seconds=wall_time,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        intake_complete=session.intake_complete,
        spec_materialized=True,
        assumptions=assumptions,
    )

    golden_path = brief_dir / "golden_spec.yaml"
    if golden_path.exists():
        golden_spec = load_spec(golden_path)
        result_kwargs["score"] = score_spec(final_spec, golden_spec)

    if brief["expect_infeasible"]:
        pools = assign_pools(final_spec)
        conflict_set: ConflictSet | None = extract_conflict(final_spec, pools)
        involves = (brief.get("golden_conflict") or {}).get("involves", [])
        mapped_families = golden_conflict_families(involves)
        result_kwargs["conflict_extracted"] = conflict_set is not None
        result_kwargs["golden_conflict_families"] = mapped_families
        if conflict_set is not None:
            extracted_families = sorted(conflict_set.groups())
            result_kwargs["conflict_summary"] = conflict_set.summary
            result_kwargs["conflict_families"] = extracted_families
            result_kwargs["conflict_families_match"] = set(mapped_families).issubset(conflict_set.groups())
        else:
            result_kwargs["conflict_families_match"] = False

    return BriefResult(**result_kwargs)


# ---------------------------------------------------------------------------
# run_corpus
# ---------------------------------------------------------------------------


def _discover_brief_dirs(briefs_dir: Path) -> list[Path]:
    return sorted(p.parent for p in briefs_dir.glob("*/brief.yaml"))


def _prompt_version() -> str:
    prompt_path = Path(prompts_module.__file__)
    return hashlib.sha256(prompt_path.read_bytes()).hexdigest()[:12]


def _aggregate(results: list[BriefResult]) -> CorpusAggregate:
    scores = [r.score for r in results if r.score is not None]
    num_skipped = sum(1 for r in results if r.skipped)
    num_incomplete = sum(1 for r in results if not r.skipped and not r.spec_materialized)
    mean_precision = sum(s.precision for s in scores) / len(scores) if scores else 0.0
    mean_recall = sum(s.recall for s in scores) / len(scores) if scores else 0.0
    mean_f1 = sum(s.f1 for s in scores) / len(scores) if scores else 0.0
    total_hallucinated = sum(s.hallucinated_count for s in scores)
    infeasible = [r for r in results if r.expect_infeasible]
    infeasible_matches = sum(1 for r in infeasible if r.conflict_families_match)
    return CorpusAggregate(
        num_briefs=len(results),
        num_scored=len(scores),
        num_skipped=num_skipped,
        num_incomplete=num_incomplete,
        mean_precision=mean_precision,
        mean_recall=mean_recall,
        mean_f1=mean_f1,
        total_hallucinated=total_hallucinated,
        infeasible_briefs=len(infeasible),
        infeasible_conflict_family_matches=infeasible_matches,
    )


def run_corpus(
    briefs_dir: Path,
    ids: list[str] | None,
    provider_name: str,
    out_path: Path,
    max_turns: int = 24,
) -> CorpusResult:
    """Run every brief under `briefs_dir` (or just `ids`), score, and write results JSON."""
    brief_dirs = _discover_brief_dirs(briefs_dir)
    if ids:
        wanted = set(ids)
        brief_dirs = [d for d in brief_dirs if d.name in wanted]

    results = [run_brief(d, provider_name=provider_name, max_turns=max_turns) for d in brief_dirs]
    aggregate = _aggregate(results)

    model = os.environ.get("TOURNEYDESK_MODEL", DEFAULT_MODEL)
    generated_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    corpus = CorpusResult(
        generated_at=generated_at,
        model=model,
        prompt_version=_prompt_version(),
        provider=provider_name,
        briefs=results,
        aggregate=aggregate,
    )

    out_path.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_")
    out_file = out_path / f"{generated_at}_{safe_model}.json"
    out_file.write_text(corpus.model_dump_json(indent=2))

    return corpus


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--briefs",
    "briefs_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default="evals/briefs",
    show_default=True,
)
@click.option("--ids", default=None, help="Comma-separated brief ids to run (default: all briefs).")
@click.option("--provider", type=click.Choice(["claude", "fake"]), default="claude", show_default=True)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="evals/results",
    show_default=True,
)
@click.option("--max-turns", "max_turns", type=int, default=24, show_default=True)
def main(briefs_dir: Path, ids: str | None, provider: str, out_dir: Path, max_turns: int) -> None:
    """Run the eval corpus (or a subset) and write a results JSON.

    --provider claude makes live Anthropic API calls (one persona + one agent
    conversation per brief) and requires ANTHROPIC_API_KEY. --provider fake
    only scores briefs whose brief.yaml ships a script/messages pair; none of
    the current corpus briefs do, so every brief is reported skipped.
    """
    id_list = [i.strip() for i in ids.split(",") if i.strip()] if ids else None
    corpus = run_corpus(briefs_dir, id_list, provider, out_dir, max_turns=max_turns)
    agg = corpus.aggregate
    click.echo(
        f"{agg.num_scored}/{agg.num_briefs} briefs scored "
        f"({agg.num_skipped} skipped, {agg.num_incomplete} incomplete) -- "
        f"mean F1={agg.mean_f1:.3f} mean precision={agg.mean_precision:.3f} mean recall={agg.mean_recall:.3f} "
        f"hallucinated={agg.total_hallucinated} "
        f"infeasible conflict matches={agg.infeasible_conflict_family_matches}/{agg.infeasible_briefs}"
    )


if __name__ == "__main__":
    main()
