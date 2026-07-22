"""Cold-start boundaries for the demo API's health and solver surfaces."""

from __future__ import annotations

import subprocess
import sys


def test_demo_api_import_defers_llm_provider_stacks() -> None:
    """Importing the ASGI app must not initialize dependencies used only by chat."""
    script = """
import sys
import demo.api.main

unexpected = [
    name
    for name in ("anthropic", "pydantic_ai", "openai")
    if name in sys.modules
]
if unexpected:
    raise SystemExit(f"eager LLM imports: {unexpected}")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_package_level_claude_export_remains_compatible() -> None:
    """The lazy package attribute preserves the historical public import."""
    from tourneydesk.providers import ClaudeIntake
    from tourneydesk.providers.claude import ClaudeIntake as ConcreteClaudeIntake

    assert ClaudeIntake is ConcreteClaudeIntake


def test_package_level_web_exports_remain_compatible() -> None:
    """Lazy web-package exports continue to support the CLI import surface."""
    from tourneydesk.web import create_app
    from tourneydesk.web.app import create_app as concrete_create_app

    assert create_app is concrete_create_app
