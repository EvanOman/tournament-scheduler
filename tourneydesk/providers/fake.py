"""Deterministic, zero-network intake provider driven by a scripted turn list.

Used by the CLI's `--provider fake` mode and by the offline end-to-end test --
it drives the exact same tools/dispatch/session path a real ClaudeIntake would,
without ever touching the network.
"""

from __future__ import annotations

import time
from typing import Any

from tourneydesk.providers.base import AgentTurn, TextDelta
from tourneydesk.session import SpecSession
from tourneydesk.tools import dispatch


class FakeIntake:
    """Scripted stand-in for ClaudeIntake.

    `script` is a list of scripted agent turns. Each turn is a dict:
        {"tool_calls": [{"name": ..., "input": {...}}, ...], "text": "..."}
    `tool_calls` and `text` are both optional (default to `[]` / `""`) so a
    turn can be tool-calls-only, text-only, or both.
    """

    def __init__(self, session: SpecSession, script: list[dict[str, Any]]) -> None:
        self.session = session
        self._script = list(script)

    async def send(self, director_message: str, on_text_delta: TextDelta | None = None) -> AgentTurn:
        if not self._script:
            text = "I have everything I need for now."
            _stream(text, on_text_delta)
            return AgentTurn(
                text=text,
                tool_calls=[],
                echoes=[],
                complete=self.session.intake_complete,
            )

        turn = self._script.pop(0)
        tool_calls: list[dict[str, Any]] = turn.get("tool_calls", [])
        text = turn.get("text", "")

        echoes: list[str] = []
        for call in tool_calls:
            result = dispatch(self.session, call["name"], call["input"])
            echoes.append(result.content)

        _stream(text, on_text_delta)
        return AgentTurn(text=text, tool_calls=tool_calls, echoes=echoes, complete=self.session.intake_complete)


def _stream(text: str, on_text_delta: TextDelta | None) -> None:
    """Emit `text` word-by-word so the web UI shows a realistic token stream.

    Runs inside the worker thread the web layer offloads `send` onto, so a tiny
    sleep here paces the stream without touching the event loop. When no sink is
    provided (CLI, tests calling send directly) this is a no-op beyond nothing.
    """
    if on_text_delta is None or not text:
        return
    words = text.split(" ")
    for i, word in enumerate(words):
        on_text_delta(word if i == 0 else " " + word)
        time.sleep(0.015)
