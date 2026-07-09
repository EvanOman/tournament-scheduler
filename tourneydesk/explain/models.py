"""Output schema for the conflict-explanation layer (M5).

`ConflictExplanation` is produced by both the deterministic and LLM paths in
`tourneydesk.explain.engine` -- the two paths share this exact shape so
callers (CLI, eventual web UI) never need to know which one ran.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SpecEditOp = Literal["update_division", "set_field_availability", "remove_constraint", "update_field", "other"]


class SpecEdit(BaseModel):
    """A machine-actionable sketch of one change to a `TournamentSpec`.

    Does not auto-apply yet -- it names the operation, the spec object it
    targets, and (when applicable) the field/value being changed, plus a
    human note. A later milestone can turn this into an actual mutation via
    `tourneydesk.session.SpecSession`.
    """

    op: SpecEditOp
    target_id: str  # a real division/field/team id from the spec (or a coach name for coaching edits)
    field: str | None = None  # the spec field being changed, e.g. "min_rest_minutes"
    new_value: str | None = None  # the suggested new value, stringified (units noted in `note` if not obvious)
    note: str  # short human-readable explanation of this edit


class RepairOption(BaseModel):
    """One concrete way to make an infeasible spec schedulable."""

    title: str  # short imperative, e.g. "Reduce U14 minimum rest to 30 minutes"
    description: str  # 2-3 sentences, plain English for a non-technical director
    tradeoff: str  # what the director gives up by taking this repair
    spec_edits: list[SpecEdit] = Field(min_length=1)


class ConflictExplanation(BaseModel):
    """A director-facing explanation of why a spec is infeasible, plus repairs."""

    headline: str  # one sentence a director understands
    narrative: str  # short paragraph explaining WHY, grounded only in the ConflictSet items
    repairs: list[RepairOption] = Field(min_length=2, max_length=3)
    grounding: list[str] = Field(min_length=1)  # verbatim ConflictItem descriptors this explanation rests on
