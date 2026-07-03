"""LLM intake interface stub.

This module defines the boundary between natural-language tournament
descriptions and the structured TournamentSpec. The actual LLM integration
is deferred to Phase 3 -- this stub documents the contract.

Architecture (from research report 07):
  1. Tournament director provides requirements via natural language
     (email, form, phone call transcript).
  2. LLM parses requirements into a draft TournamentSpec.
  3. Draft is validated and presented to the director for confirmation.
  4. Confirmed spec goes to the solver.

The key insight: LLMs are the interface layer, not the optimization layer.
The solver does the hard mathematical work. The LLM makes the solver
accessible to non-technical users.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from tournament_scheduler.models import TournamentSpec


class IntakeProvider(ABC):
    """Abstract interface for converting unstructured input to a TournamentSpec.

    Implementations could include:
    - ClaudeIntake: Uses Claude API for natural language parsing
    - FormIntake: Structured form/spreadsheet parsing
    - MockIntake: For testing
    """

    @abstractmethod
    async def parse(self, raw_input: str, context: dict[str, Any] | None = None) -> IntakeResult:
        """Parse raw input into a tournament spec.

        Args:
            raw_input: Natural language description, email text, or form data.
            context: Optional context (e.g. previous tournament specs for the
                     same director, known field configurations, etc.)

        Returns:
            IntakeResult with the parsed spec and any clarification questions.
        """
        ...

    @abstractmethod
    async def clarify(self, result: IntakeResult, answers: dict[str, str]) -> IntakeResult:
        """Refine a parse result based on answers to clarification questions.

        Args:
            result: The previous IntakeResult that had questions.
            answers: Map of question_id -> answer text.

        Returns:
            Updated IntakeResult, possibly with more questions or a complete spec.
        """
        ...


class IntakeResult:
    """Result of an intake parse attempt.

    Attributes:
        spec: The parsed TournamentSpec, or None if parsing is incomplete.
        confidence: 0.0-1.0 confidence in the parse.
        questions: List of clarification questions to ask the director.
        warnings: List of potential issues detected in the input.
        raw_constraints: The extracted constraints before mapping to the schema,
                        for human review.
    """

    def __init__(
        self,
        spec: TournamentSpec | None = None,
        confidence: float = 0.0,
        questions: list[dict[str, str]] | None = None,
        warnings: list[str] | None = None,
        raw_constraints: list[dict[str, str]] | None = None,
    ):
        self.spec = spec
        self.confidence = confidence
        self.questions = questions or []
        self.warnings = warnings or []
        self.raw_constraints = raw_constraints or []

    @property
    def is_complete(self) -> bool:
        """Whether the parse produced a usable spec with no outstanding questions."""
        return self.spec is not None and len(self.questions) == 0

    @property
    def needs_clarification(self) -> bool:
        """Whether there are outstanding questions for the director."""
        return len(self.questions) > 0
