"""FastAPI web frontend over the shared IntakeService (M3+M4 vertical slice)."""

from __future__ import annotations

from tourneydesk.web.app import claude_factory, create_app, fake_factory

__all__ = ["create_app", "claude_factory", "fake_factory"]
