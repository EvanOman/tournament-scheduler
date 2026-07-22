"""FastAPI web frontend over the shared IntakeService (M3+M4 vertical slice)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tourneydesk.web.app import agent_sdk_factory, claude_factory, create_app, fake_factory

__all__ = ["create_app", "agent_sdk_factory", "claude_factory", "fake_factory"]


def __getattr__(name: str) -> Any:
    """Defer the full web/provider stack until a package-level export is used."""
    if name in __all__:
        from tourneydesk.web import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
