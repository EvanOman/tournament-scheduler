"""IntakeService: the single service layer both the CLI and the future web app drive.

Neither the terminal chat loop nor the (M3) FastAPI/WebSocket handler should
own conversation logic, spec materialization, or solve orchestration directly
-- both instantiate this class and call its methods. That keeps CLI/web
parity structural rather than aspirational: there is exactly one place that
knows how to run a turn, read back the rules JSON, or attempt a speculative
solve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from tournament_scheduler.models import TournamentSchedule, TournamentSpec
from tournament_scheduler.pools import assign_pools
from tournament_scheduler.solver import solve
from tournament_scheduler.validator import ValidationResult, validate
from tourneydesk.providers.base import AgentTurn, IntakeProvider, OnTurn, Persona, run_conversation
from tourneydesk.session import IncompleteSpecError, SpecSession

SolveStatus = Literal["incomplete", "infeasible", "invalid", "solved"]


@dataclass
class SolveOutcome:
    """Result of a speculative solve attempt against the current draft."""

    status: SolveStatus
    missing: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    schedule: TournamentSchedule | None = None
    validation: ValidationResult | None = None

    @property
    def ok(self) -> bool:
        return self.status == "solved"


class IntakeService:
    """Owns one conversation: a SpecSession plus the provider driving it.

    The CLI and the web app both construct exactly this class per session and
    call the same methods -- `send` to advance the conversation one turn,
    `rules_json`/`to_spec` to read the draft, `try_solve` for a speculative
    schedule, `run_conversation` to drive persona<->provider to completion.
    """

    def __init__(self, provider: IntakeProvider) -> None:
        self.provider = provider

    @property
    def session(self) -> SpecSession:
        return self.provider.session

    async def send(self, director_message: str) -> AgentTurn:
        return await self.provider.send(director_message)

    async def run_conversation(
        self, persona: Persona, max_turns: int = 20, on_turn: OnTurn | None = None
    ) -> SpecSession:
        return await run_conversation(self.provider, persona, max_turns=max_turns, on_turn=on_turn)

    @property
    def complete(self) -> bool:
        return self.session.intake_complete

    def rules_json(self) -> dict[str, object]:
        return self.session.to_rules_json()

    def to_spec(self) -> tuple[TournamentSpec, list[str]]:
        return self.session.to_spec()

    def try_solve(self) -> SolveOutcome:
        """Attempt a speculative solve against the current draft.

        Never raises: an incomplete draft or an infeasible/invalid schedule
        all come back as a SolveOutcome with the right status, so callers
        (CLI or web) can render a status without their own try/except.
        """
        try:
            spec, assumptions = self.session.to_spec()
        except IncompleteSpecError as exc:
            return SolveOutcome(status="incomplete", missing=exc.missing)

        pools = assign_pools(spec)
        schedule = solve(spec, pools)
        if schedule.stats.status not in ("OPTIMAL", "FEASIBLE"):
            return SolveOutcome(status="infeasible", assumptions=assumptions, schedule=schedule)

        result = validate(schedule, spec)
        status: SolveStatus = "solved" if result.valid else "invalid"
        return SolveOutcome(status=status, assumptions=assumptions, schedule=schedule, validation=result)
