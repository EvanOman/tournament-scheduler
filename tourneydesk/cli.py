"""tourneydesk chat --brief FILE --provider [claude|fake] [--max-turns N]

Thin terminal frontend over `tourneydesk.core.IntakeService` -- the same
service class the future FastAPI/WebSocket app (M3) will instantiate per
session. This module owns no conversation logic of its own: it builds a
provider + persona from the brief file, hands them to IntakeService, and
renders what comes back.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.panel import Panel

from tourneydesk.core import IntakeService
from tourneydesk.persona import ClaudePersona, FakePersona
from tourneydesk.providers.base import AgentTurn, Persona
from tourneydesk.providers.claude import ClaudeIntake
from tourneydesk.providers.fake import FakeIntake
from tourneydesk.session import SpecSession

console = Console()


def _find_free_port(preferred: int, host: str = "127.0.0.1", tries: int = 50) -> int:
    """Return `preferred` if free, else the next free high port. Never kills anything."""
    for candidate in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise click.ClickException(f"No free port found in {preferred}..{preferred + tries}.")


@click.group()
def main() -> None:
    """TourneyDesk: conversational tournament intake."""


@main.command()
@click.option("--brief", "brief_path", type=click.Path(exists=True), required=True, help="Brief YAML file.")
@click.option("--provider", type=click.Choice(["claude", "fake"]), default="claude")
@click.option("--max-turns", "max_turns", type=int, default=20)
def chat(brief_path: str, provider: str, max_turns: int) -> None:
    """Run an intake conversation against a brief file and print the outcome."""
    asyncio.run(_run_chat(brief_path, provider, max_turns))


def _build_provider_and_persona(brief: dict[str, Any], provider_name: str, session: SpecSession) -> tuple[Any, Persona]:
    if provider_name == "claude":
        persona_text = f"{brief.get('persona', '')}\n\n{brief.get('facts', '')}".strip()
        return ClaudeIntake(session), ClaudePersona(persona_text)

    script = brief.get("script")
    messages = brief.get("messages")
    if not script or not messages:
        raise click.ClickException(
            "--provider fake requires the brief file to include 'script' (a list of FakeIntake "
            "turns) and 'messages' (a list of FakePersona lines)."
        )
    return FakeIntake(session, script), FakePersona(messages)


def _print_turn(director_message: str, turn: AgentTurn) -> None:
    console.print(f"[bold cyan]Director:[/bold cyan] {director_message}")
    for echo in turn.echoes:
        console.print(f"  [dim]· {echo}[/dim]")
    if turn.text:
        console.print(f"[bold green]TourneyDesk:[/bold green] {turn.text}")


@main.command()
@click.option("--port", type=int, default=18780, help="Preferred port (auto-bumps if taken; never kills).")
@click.option("--host", default="127.0.0.1", help="Host/interface to bind.")
@click.option(
    "--provider",
    type=click.Choice(["claude", "fake"]),
    default="claude",
    help="claude = real intake agent (needs ANTHROPIC_API_KEY); fake = offline canned demo.",
)
@click.option("--db", "db_path", default="tourneydesk.db", help="SQLite file for session persistence.")
def serve(port: int, host: str, provider: str, db_path: str) -> None:
    """Serve the TourneyDesk web app (SPA + REST + WebSocket)."""
    import uvicorn

    from tourneydesk.web import claude_factory, create_app, fake_factory

    factory = fake_factory if provider == "fake" else claude_factory
    chosen = _find_free_port(port, host=host)
    if chosen != port:
        console.print(f"[yellow]Port {port} is busy; using {chosen} instead (nothing was killed).[/yellow]")
    app = create_app(db_path=db_path, provider_factory=factory)
    console.print(f"[bold green]TourneyDesk[/bold green] serving on http://{host}:{chosen}  (provider={provider})")
    uvicorn.run(app, host=host, port=chosen, log_level="info")


async def _run_chat(brief_path: str, provider_name: str, max_turns: int) -> None:
    brief = yaml.safe_load(Path(brief_path).read_text()) or {}
    session = SpecSession()
    provider, persona = _build_provider_and_persona(brief, provider_name, session)
    service = IntakeService(provider)

    await service.run_conversation(persona, max_turns=max_turns, on_turn=_print_turn)

    console.print()
    console.print(Panel(json.dumps(service.rules_json(), indent=2, default=str), title="Rules JSON"))
    try:
        _, assumptions = service.to_spec()
        if assumptions:
            console.print(Panel("\n".join(assumptions), title="Assumptions"))
        else:
            console.print("[green]No assumptions were needed -- every fact was stated explicitly.[/green]")
    except Exception as exc:
        console.print(f"[yellow]Spec is not complete yet:[/yellow] {exc}")


if __name__ == "__main__":
    main()
