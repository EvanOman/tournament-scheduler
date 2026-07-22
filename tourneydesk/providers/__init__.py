"""Conversational providers for the TourneyDesk intake agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tourneydesk.providers.base import AgentTurn, IntakeProvider, Persona, run_conversation
from tourneydesk.providers.fake import FakeIntake

if TYPE_CHECKING:
    from tourneydesk.providers.claude import ClaudeIntake

__all__ = [
    "AgentTurn",
    "IntakeProvider",
    "Persona",
    "run_conversation",
    "FakeIntake",
    "ClaudeIntake",
]


def __getattr__(name: str) -> Any:
    """Preserve the package-level ClaudeIntake export without eager SDK import."""
    if name == "ClaudeIntake":
        from tourneydesk.providers.claude import ClaudeIntake

        return ClaudeIntake
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
