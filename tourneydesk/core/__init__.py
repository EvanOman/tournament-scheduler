"""Shared service layer: the one path CLI and (future) web frontends both drive."""

from __future__ import annotations

from tourneydesk.core.service import IntakeService, SolveOutcome

__all__ = ["IntakeService", "SolveOutcome"]
