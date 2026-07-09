"""Simulated tournament director for the CLI harness and evals.

FakePersona is deterministic and network-free. ClaudePersona is correct per
DESIGN.md sec 3 but not exercised by the offline test suite.
"""

from __future__ import annotations

import os
from typing import Any

import anthropic

DEFAULT_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 1024

_PERSONA_SYSTEM_TEMPLATE = """\
You are role-playing as a youth-sports tournament director talking to an intake \
assistant that is gathering the facts needed to build your tournament schedule.

Stay fully in character. Only use the facts below -- never invent facts that \
aren't stated here, and never volunteer everything at once; reveal facts \
naturally in response to what the assistant asks or says, the way a real \
director would in conversation.

Persona:
{persona}

Facts you know (the only source you may draw from):
{facts}

When the assistant summarizes the plan back to you and it matches these facts, \
confirm it plainly (e.g. "Yes, that's right, go ahead"). Keep each message short \
and conversational -- you are texting/chatting, not writing an email.
"""


class FakePersona:
    """Deterministic, network-free persona driven by a scripted line list."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.done = False

    async def reply(self, agent_text: str) -> str:
        if not self._messages:
            self.done = True
            return ""
        message = self._messages.pop(0)
        if not self._messages:
            self.done = True
        return message


class ClaudePersona:
    """A separate Claude call, prompted from a brief's persona + facts."""

    def __init__(self, brief_text: str, model: str | None = None) -> None:
        self.brief_text = brief_text
        self.model = model or os.environ.get("TOURNEYDESK_MODEL", DEFAULT_MODEL)
        self.done = False
        self._client = anthropic.Anthropic()
        self._history: list[dict[str, Any]] = []
        self._system = _PERSONA_SYSTEM_TEMPLATE.format(persona=brief_text, facts=brief_text)

    async def reply(self, agent_text: str) -> str:
        if agent_text:
            self._history.append({"role": "user", "content": agent_text})
        else:
            self._history.append({"role": "user", "content": "Let's get started."})

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": _MAX_TOKENS,
            "system": self._system,
            "messages": self._history,
        }
        if not self.model.startswith("claude-fable"):
            request_kwargs["thinking"] = {"type": "adaptive"}

        with self._client.messages.stream(**request_kwargs) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "refusal":
            self.done = True
            return "Never mind, I'll follow up another time."

        self._history.append({"role": "assistant", "content": response.content})
        text = "\n".join(block.text for block in response.content if block.type == "text")
        return text
