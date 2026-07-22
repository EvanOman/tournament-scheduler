"""Turn a `ConflictSet` into a director-facing `ConflictExplanation` (M5).

Two paths produce the same `ConflictExplanation` shape:

- `_deterministic_explanation` -- always available, zero network, rule-based
  repairs derived from the constraint families present in the conflict.
- `_llm_explanation` -- a single non-conversational Claude call with a
  structured-output schema, used when explicitly requested or when
  `ANTHROPIC_API_KEY` is set. Anti-confabulation guard: the model's
  `grounding` list is checked against the conflict's actual descriptors
  (`_validate_grounding`); any mismatch falls back to the deterministic path.

The Anthropic SDK and client are both loaded only inside `_llm_explanation`.
Deterministic explanations therefore do not pay the provider import cost.
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

from tournament_scheduler.conflict import ConflictSet, extract_conflict
from tournament_scheduler.models import DivisionSpec, FieldSize, FieldSpec, TeamSpec, TournamentSpec
from tournament_scheduler.pools import assign_pools
from tourneydesk.explain.models import ConflictExplanation, RepairOption, SpecEdit
from tourneydesk.explain.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 4096


class _RefusalError(Exception):
    """Raised when the model declines to answer (`stop_reason == "refusal"`)."""


class _UngroundedExplanationError(Exception):
    """Raised when an LLM explanation's `grounding` doesn't trace to the conflict's descriptors.

    This is the anti-confabulation guard: every `grounding` entry must be an
    exact, verbatim `ConflictItem.descriptor` from the input conflict. Anything
    else means the model asserted something not actually established by the
    solver's unsat core, and the caller must fall back to the deterministic path.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def explain_infeasible_spec(
    spec: TournamentSpec,
    *,
    time_limit_s: float = 10.0,
    use_llm: bool | None = None,
) -> ConflictExplanation | None:
    """Solve `spec`, and if infeasible, return an explanation. `None` if feasible."""
    pools = assign_pools(spec)
    conflict = extract_conflict(spec, pools, time_limit_s=time_limit_s)
    if conflict is None:
        return None
    return explain_conflict(spec, conflict, use_llm=use_llm)


def explain_conflict(
    spec: TournamentSpec,
    conflict: ConflictSet,
    *,
    use_llm: bool | None = None,
) -> ConflictExplanation:
    """Explain a known conflict. Tries the LLM path when requested/available, else deterministic.

    `use_llm=True` forces the LLM path (raising back up only if it fails after
    fallback would also fail -- in practice it never raises, see below).
    `use_llm=False` forces the deterministic path (no network, no API key
    needed). `use_llm=None` (default) uses the LLM path iff `ANTHROPIC_API_KEY`
    is set in the environment.
    """
    want_llm = use_llm if use_llm is not None else bool(os.environ.get("ANTHROPIC_API_KEY"))
    if want_llm:
        try:
            return _llm_explanation(spec, conflict)
        except Exception:
            logger.warning("LLM conflict explanation failed; falling back to deterministic path.", exc_info=True)
    return _deterministic_explanation(spec, conflict)


# ---------------------------------------------------------------------------
# Deterministic path
# ---------------------------------------------------------------------------


def _deterministic_explanation(spec: TournamentSpec, conflict: ConflictSet) -> ConflictExplanation:
    repairs = _build_repairs(spec, conflict)
    if len(repairs) < 2:
        repairs = (repairs + _generic_repairs(spec, conflict))[:3]
    return ConflictExplanation(
        headline=conflict.summary,
        narrative=_narrative(conflict),
        repairs=repairs[:3],
        grounding=[item.descriptor for item in conflict.involves],
    )


def _narrative(conflict: ConflictSet) -> str:
    facts = " ".join(f"{item.descriptor}." for item in conflict.involves)
    text = f"{conflict.summary} {facts}".strip()
    if not conflict.minimal:
        text += " (This conflict set may not be fully minimal -- the analysis was time-boxed.)"
    return text


def _build_repairs(spec: TournamentSpec, conflict: ConflictSet) -> list[RepairOption]:
    """Generate up to 3 repair options, most-viable first.

    Ordering rule: constraint families are visited in a fixed priority order
    -- rest, availability, field capacity (field_double_booking/assignment),
    coaching -- reflecting roughly increasing director cost: loosening a
    numeric requirement (rest, games_per_team) is cheaper than reworking a
    field's hours, which is cheaper than securing a whole new field or a
    second coach. Within each family, its own repairs are already
    most-viable-first. `field_double_booking` and `assignment` share one
    handler (both point at the same underlying field-capacity shortfall) so
    it only runs once. Collection stops once 3 repairs are gathered.
    """
    groups = conflict.groups()
    handlers: list[tuple[bool, Any]] = [
        ("rest" in groups, _rest_repairs),
        ("availability" in groups, _availability_repairs),
        ("field_double_booking" in groups or "assignment" in groups, _capacity_repairs),
        ("coaching" in groups, _coaching_repairs),
    ]

    repairs: list[RepairOption] = []
    seen_handlers: set[int] = set()
    seen_titles: set[str] = set()
    seen_edit_keys: set[tuple[str, str, str | None]] = set()
    for present, handler in handlers:
        if not present or id(handler) in seen_handlers:
            continue
        seen_handlers.add(id(handler))
        for repair in handler(spec, conflict):
            # Dedupe: skip a repair whose title, or whose every spec-edit
            # (op, target, field) triple, was already proposed by an earlier
            # family (e.g. rest and capacity both suggesting "extend Field 1").
            edit_keys = {(e.op, e.target_id, e.field) for e in repair.spec_edits}
            if repair.title in seen_titles or edit_keys <= seen_edit_keys:
                continue
            seen_titles.add(repair.title)
            seen_edit_keys.update(edit_keys)
            repairs.append(repair)
        if len(repairs) >= 3:
            break
    return repairs[:3]


def _find_division(spec: TournamentSpec, division_id: str | None) -> DivisionSpec | None:
    if division_id is None:
        return None
    return next((d for d in spec.divisions if d.id == division_id), None)


def _find_team(spec: TournamentSpec, team_id: str) -> TeamSpec | None:
    return next((t for t in spec.teams if t.id == team_id), None)


def _find_field(spec: TournamentSpec, field_id: str) -> FieldSpec | None:
    return next((f for f in spec.fields if f.id == field_id), None)


def _field_for_size(spec: TournamentSpec, size: FieldSize) -> FieldSpec | None:
    return next((f for f in spec.fields if f.size == size), None)


def _rest_repairs(spec: TournamentSpec, conflict: ConflictSet) -> list[RepairOption]:
    item = next((it for it in conflict.involves if it.group == "rest"), None)
    if item is None or len(item.spec_ids) < 2:
        return []
    team_id, division_id = item.spec_ids[0], item.spec_ids[1]
    division = _find_division(spec, division_id)
    if division is None:
        return []
    team = _find_team(spec, team_id)
    team_label = team.name if team is not None else team_id

    current = division.min_rest_minutes
    suggested = max(15, current // 2)
    repairs = [
        RepairOption(
            title=f"Reduce {division.name} minimum rest to {suggested} minutes",
            description=(
                f"{division.name} currently requires {current} minutes of rest between a team's "
                f"games, which is why {team_label} can't be fit into the available time. Lowering "
                f"the requirement to {suggested} minutes gives the solver enough slack to schedule "
                "every game."
            ),
            tradeoff=(
                f"Teams in {division.name} get less recovery time between games "
                f"({current} down to {suggested} minutes)."
            ),
            spec_edits=[
                SpecEdit(
                    op="update_division",
                    target_id=division.id,
                    field="min_rest_minutes",
                    new_value=str(suggested),
                    note=f"Halve the current {current}-minute minimum rest requirement (floored at 15 minutes).",
                )
            ],
        )
    ]

    field = _field_for_size(spec, division.field_size)
    if field is not None:
        shortfall = max(15, current - suggested)
        repairs.append(
            RepairOption(
                title=f"Extend {field.name}'s hours by about {shortfall} minutes",
                description=(
                    f"Instead of shortening rest, keep {division.name}'s {current}-minute rest "
                    f"requirement and give the schedule more room by extending {field.name}'s "
                    f"availability by roughly {shortfall} minutes."
                ),
                tradeoff=f"The tournament day runs about {shortfall} minutes longer on {field.name}.",
                spec_edits=[
                    SpecEdit(
                        op="set_field_availability",
                        target_id=field.id,
                        field="availability",
                        new_value=f"+{shortfall}min",
                        note=f"Extend an existing availability window on {field.name} by roughly {shortfall} minutes.",
                    )
                ],
            )
        )
    return repairs


def _availability_repairs(spec: TournamentSpec, conflict: ConflictSet) -> list[RepairOption]:
    item = next((it for it in conflict.involves if it.group == "availability"), None)
    if item is None:
        return []
    division_id = item.spec_ids[2] if len(item.spec_ids) >= 3 else None
    division = _find_division(spec, division_id) or spec.divisions[0]

    repairs: list[RepairOption] = []
    field = _field_for_size(spec, division.field_size)
    if field is not None:
        repairs.append(
            RepairOption(
                title=f"Add more availability hours to {field.name}",
                description=(
                    f"No {division.field_size.value}-sized field has an open window that fits every "
                    f"{division.name} matchup. Opening up more hours on {field.name} gives those "
                    "games somewhere to go."
                ),
                tradeoff=f"{field.name} needs to be booked for a longer part of the day.",
                spec_edits=[
                    SpecEdit(
                        op="set_field_availability",
                        target_id=field.id,
                        field="availability",
                        new_value="extend window",
                        note=(
                            f"Add or extend an availability window on {field.name} sized for "
                            f"{division.field_size.value} games."
                        ),
                    )
                ],
            )
        )
    repairs.append(
        RepairOption(
            title=f"Add another {division.field_size.value} field",
            description=(
                f"{division.name} needs a {division.field_size.value}-sized field and there isn't "
                "enough open time on the existing ones. A second field of that size gives the "
                "solver somewhere else to put the remaining games."
            ),
            tradeoff="Requires securing and paying for an additional field.",
            spec_edits=[
                SpecEdit(
                    op="other",
                    target_id=division.id,
                    field=None,
                    new_value=None,
                    note=(
                        f"Add a new field with size={division.field_size.value} and availability "
                        "covering the tournament window."
                    ),
                )
            ],
        )
    )
    return repairs


def _capacity_repairs(spec: TournamentSpec, conflict: ConflictSet) -> list[RepairOption]:
    field_ids = [it.spec_ids[0] for it in conflict.involves if it.group == "field_double_booking" and it.spec_ids]
    division_ids = [
        it.spec_ids[2]
        for it in conflict.involves
        if it.group in ("assignment", "availability") and len(it.spec_ids) >= 3
    ]
    division = (_find_division(spec, division_ids[0]) if division_ids else None) or spec.divisions[0]
    field = (
        (_find_field(spec, field_ids[0]) if field_ids else None)
        or _field_for_size(spec, division.field_size)
        or spec.fields[0]
    )

    slot_minutes = spec.total_game_minutes(division)
    n_teams = len(spec.teams_in_division(division.id))
    total_games = max(1, math.ceil(n_teams * division.games_per_team / 2))
    needed_minutes = total_games * slot_minutes
    matching_fields = [f for f in spec.fields if f.size == division.field_size]
    available_minutes = sum(
        int((window.end - window.start).total_seconds() // 60) for f in matching_fields for window in f.availability
    )
    shortfall_minutes = max(0, needed_minutes - available_minutes)
    extra_hours = max(1, math.ceil(shortfall_minutes / 60))

    repairs: list[RepairOption] = []
    # Only pitch capacity-adding repairs (extend hours / add a field) when raw
    # field-minutes genuinely fall short of the games' needs. When they don't
    # (e.g. the true bottleneck is a rest requirement that dragged `assignment`
    # into the core), extra parallel capacity would not fix anything and the
    # numbers in the description would be contradictory -- the only capacity
    # lever that still helps then is playing fewer games per team.
    if shortfall_minutes > 0:
        repairs.append(
            RepairOption(
                title=f"Extend {field.name}'s hours by about {extra_hours}h",
                description=(
                    f"{division.name} needs roughly {needed_minutes} minutes of field time for "
                    f"{total_games} games, but the {division.field_size.value} fields only offer about "
                    f"{available_minutes} minutes today. Adding about {extra_hours} hour(s) to "
                    f"{field.name} closes most of that gap."
                ),
                tradeoff=(
                    f"{field.name} is booked for {extra_hours} more hour(s), which may push into another "
                    "event or require lights."
                ),
                spec_edits=[
                    SpecEdit(
                        op="set_field_availability",
                        target_id=field.id,
                        field="availability",
                        new_value=f"+{extra_hours}h",
                        note=f"Extend {field.name}'s availability window by about {extra_hours} hour(s).",
                    )
                ],
            )
        )
        repairs.append(
            RepairOption(
                title=f"Add another {division.field_size.value} field",
                description=(
                    f"A second {division.field_size.value}-sized field running for the same window "
                    f"roughly doubles available capacity, comfortably covering {division.name}'s "
                    f"{total_games} games."
                ),
                tradeoff="Requires securing, staffing, and paying for an additional field.",
                spec_edits=[
                    SpecEdit(
                        op="other",
                        target_id=division.id,
                        field=None,
                        new_value=None,
                        note=(
                            f"Add a new field with size={division.field_size.value} and availability "
                            "covering the tournament window."
                        ),
                    )
                ],
            )
        )
    if division.games_per_team > 1:
        reduced = division.games_per_team - 1
        repairs.append(
            RepairOption(
                title=f"Reduce {division.name} to {reduced} pool-play game(s) per team",
                description=(
                    f"Cutting {division.name} from {division.games_per_team} to {reduced} games per "
                    "team reduces the total field time needed without touching field hours at all."
                ),
                tradeoff=f"Teams in {division.name} play {reduced} pool game(s) instead of {division.games_per_team}.",
                spec_edits=[
                    SpecEdit(
                        op="update_division",
                        target_id=division.id,
                        field="games_per_team",
                        new_value=str(reduced),
                        note=f"Lower games_per_team from {division.games_per_team} to {reduced}.",
                    )
                ],
            )
        )
    return repairs


def _coaching_repairs(spec: TournamentSpec, conflict: ConflictSet) -> list[RepairOption]:
    item = next((it for it in conflict.involves if it.group == "coaching"), None)
    if item is None or len(item.spec_ids) < 2:
        return []
    team_ids = list(item.spec_ids)
    coach = next((c for c in spec.coaching_conflicts if set(c.team_ids) & set(team_ids)), None)
    coach_name = coach.coach_name if coach is not None else "the shared coach"
    team_a = _find_team(spec, team_ids[0])
    team_b = _find_team(spec, team_ids[1])
    team_a_label = team_a.name if team_a is not None else team_ids[0]
    team_b_label = team_b.name if team_b is not None else team_ids[1]
    division = (_find_division(spec, team_a.division_id) if team_a is not None else None) or spec.divisions[0]
    field = _field_for_size(spec, division.field_size) or spec.fields[0]

    return [
        RepairOption(
            title=f"Extend the schedule window so {coach_name}'s games don't overlap",
            description=(
                f"{coach_name} coaches both {team_a_label} and {team_b_label}, and the current "
                "schedule forces their games to run at the same time. Opening up more hours gives "
                "the solver room to play them one after another instead."
            ),
            tradeoff="The tournament day runs longer to fit both teams' games sequentially.",
            spec_edits=[
                SpecEdit(
                    op="set_field_availability",
                    target_id=field.id,
                    field="availability",
                    new_value="extend window",
                    note=(
                        f"Extend availability so {coach_name}'s teams can be scheduled sequentially "
                        "rather than simultaneously."
                    ),
                )
            ],
        ),
        RepairOption(
            title=f"Assign a second coach to one of {coach_name}'s teams",
            description=(
                f"If {team_b_label} has its own coach for the day, its games no longer need to "
                f"avoid overlapping with {team_a_label}'s, removing the constraint entirely."
            ),
            tradeoff="Requires finding and briefing a second coach for tournament day.",
            spec_edits=[
                SpecEdit(
                    op="remove_constraint",
                    target_id=coach_name,
                    field="coaching_conflicts",
                    new_value=None,
                    note=f"Remove the coaching conflict for {coach_name} once a second coach covers one team.",
                )
            ],
        ),
    ]


def _generic_repairs(spec: TournamentSpec, conflict: ConflictSet) -> list[RepairOption]:
    """Fallback used only when no known constraint family matched (should be rare)."""
    first_item = conflict.involves[0] if conflict.involves else None
    target_id = first_item.spec_ids[0] if first_item is not None and first_item.spec_ids else spec.divisions[0].id
    field = spec.fields[0]
    return [
        RepairOption(
            title="Loosen the tightest requirement identified above",
            description=(
                "The scheduler cannot satisfy every hard requirement at once. Revisiting the "
                "requirement described above -- relaxing it, or giving the schedule more time or "
                "fields -- is the most direct way to make the tournament schedulable."
            ),
            tradeoff="Some requirement the director cares about has to give a little.",
            spec_edits=[
                SpecEdit(
                    op="other",
                    target_id=target_id,
                    field=None,
                    new_value=None,
                    note="Review the conflicting requirement and relax it, or add more field/time capacity.",
                )
            ],
        ),
        RepairOption(
            title=f"Add more hours to {field.name}",
            description=(
                "Increasing available field hours generally resolves scheduling conflicts caused by "
                "tight time windows, at the cost of a longer tournament day."
            ),
            tradeoff="The tournament day (or number of fields) grows.",
            spec_edits=[
                SpecEdit(
                    op="set_field_availability",
                    target_id=field.id,
                    field="availability",
                    new_value="extend window",
                    note=f"Extend {field.name}'s availability window.",
                )
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------


def _spec_digest(spec: TournamentSpec) -> dict[str, Any]:
    """Compact, LLM-friendly summary of the spec: divisions, fields, team counts."""
    return {
        "name": spec.name,
        "divisions": [
            {
                "id": d.id,
                "name": d.name,
                "field_size": d.field_size.value,
                "game_duration_minutes": d.game_duration_minutes,
                "halftime_minutes": d.halftime_minutes,
                "buffer_minutes": d.buffer_minutes,
                "min_rest_minutes": d.min_rest_minutes,
                "games_per_team": d.games_per_team,
                "pool_size": d.pool_size,
                "team_count": len(spec.teams_in_division(d.id)),
            }
            for d in spec.divisions
        ],
        "fields": [
            {
                "id": f.id,
                "name": f.name,
                "size": f.size.value,
                "availability": [{"start": w.start.isoformat(), "end": w.end.isoformat()} for w in f.availability],
            }
            for f in spec.fields
        ],
        "coaching_conflicts": [{"coach_name": c.coach_name, "team_ids": c.team_ids} for c in spec.coaching_conflicts],
        "total_teams": len(spec.teams),
    }


def _validate_grounding(explanation: ConflictExplanation, conflict: ConflictSet) -> None:
    """Anti-confabulation guard: every grounding entry must be a real, verbatim conflict descriptor."""
    valid_descriptors = {item.descriptor for item in conflict.involves}
    if not explanation.grounding:
        raise _UngroundedExplanationError("explanation has no grounding entries")
    ungrounded = [g for g in explanation.grounding if g not in valid_descriptors]
    if ungrounded:
        raise _UngroundedExplanationError(f"ungrounded grounding entries: {ungrounded!r}")


def _llm_explanation(spec: TournamentSpec, conflict: ConflictSet) -> ConflictExplanation:
    """Single non-conversational structured-output call. Raises on any failure; caller falls back."""
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("TOURNEYDESK_MODEL", DEFAULT_MODEL)

    payload = {"conflict": conflict.model_dump(mode="json"), "spec_digest": _spec_digest(spec)}

    request_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": json.dumps(payload)}],
        # Structured outputs (DESIGN.md sec 3): installed anthropic SDK (0.112.0)
        # supports `output_config` with a JSON-schema format directly.
        "output_config": {"format": {"type": "json_schema", "schema": ConflictExplanation.model_json_schema()}},
    }
    # claude-fable-* rejects an explicit `thinking` param; every other model gets adaptive thinking.
    if not model.startswith("claude-fable"):
        request_kwargs["thinking"] = {"type": "adaptive"}

    response = client.messages.create(**request_kwargs)

    logger.info(
        "tourneydesk explain call: model=%s stop_reason=%s input_tokens=%s output_tokens=%s",
        response.model,
        response.stop_reason,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

    # Always check stop_reason before reading content -- a refusal can carry
    # an empty or partial content array.
    if response.stop_reason == "refusal":
        raise _RefusalError("model declined to explain this conflict")

    text_blocks = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    if not text_blocks:
        raise ValueError("structured-output response had no text content")

    data = json.loads("\n".join(text_blocks))
    explanation = ConflictExplanation.model_validate(data)
    _validate_grounding(explanation, conflict)
    return explanation
