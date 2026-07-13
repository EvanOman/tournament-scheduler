"""FastAPI web frontend over the shared IntakeService (M3+M4 vertical slice)."""

from __future__ import annotations

from tourneydesk.web.app import agent_sdk_factory, claude_factory, create_app, fake_factory

__all__ = ["create_app", "agent_sdk_factory", "claude_factory", "fake_factory"]
