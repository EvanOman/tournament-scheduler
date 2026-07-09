"""Deterministic, zero-network intake provider driven by a scripted turn list.

Used by the CLI's `--provider fake` mode and by the offline end-to-end test --
it drives the exact same tools/dispatch/session path a real ClaudeIntake would,
without ever touching the network.
"""

from __future__ import annotations

from typing import Any

from tourneydesk.providers.base import AgentTurn
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

    async def send(self, director_message: str) -> AgentTurn:
        if not self._script:
            return AgentTurn(
                text="(no more scripted turns)",
                tool_calls=[],
                echoes=[],
                complete=self.session.intake_complete,
            )

        turn = self._script.pop(0)
        tool_calls: list[dict[str, Any]] = turn.get("tool_calls", [])
        text: str = turn.get("text", "")

        echoes: list[str] = []
        for call in tool_calls:
            result = dispatch(self.session, call["name"], call["input"])
            echoes.append(result.content)

        return AgentTurn(text=text, tool_calls=tool_calls, echoes=echoes, complete=self.session.intake_complete)
