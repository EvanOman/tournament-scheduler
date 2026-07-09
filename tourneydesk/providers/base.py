"""Conversational provider contract shared by ClaudeIntake and FakeIntake."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from tourneydesk.session import SpecSession


@dataclass
class AgentTurn:
    """One reply from the intake agent for a single director message."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    echoes: list[str] = field(default_factory=list)
    complete: bool = False


@runtime_checkable
class IntakeProvider(Protocol):
    """Contract for anything that drives a SpecSession from director messages."""

    session: SpecSession

    async def send(self, director_message: str) -> AgentTurn: ...


@runtime_checkable
class Persona(Protocol):
    """Contract for a simulated (or real) tournament director."""

    done: bool

    async def reply(self, agent_text: str) -> str: ...


OnTurn = Callable[[str, AgentTurn], "Awaitable[None] | None"]


async def run_conversation(
    provider: IntakeProvider,
    persona: Persona,
    max_turns: int = 20,
    on_turn: OnTurn | None = None,
) -> SpecSession:
    """Alternate persona -> provider until the provider reports complete or max_turns.

    The persona is asked for its opening line with `agent_text=""`. This is the
    single conversation-driving loop shared by every frontend (CLI, and later
    the web app's WebSocket handler) via `IntakeService.run_conversation` --
    neither should reimplement it.

    `on_turn`, if given, is called after each turn with `(director_message,
    AgentTurn)` -- e.g. to print to a terminal or push a WebSocket message. It
    may be sync or async.
    """
    agent_text = ""
    for _ in range(max_turns):
        if persona.done:
            break
        director_message = await persona.reply(agent_text)
        turn = await provider.send(director_message)
        if on_turn is not None:
            maybe_awaitable = on_turn(director_message, turn)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        agent_text = turn.text
        if turn.complete:
            break
    return provider.session
