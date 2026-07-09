"""Live intake provider backed by the official Anthropic SDK.

Correct per DESIGN.md sec 3 (model from TOURNEYDESK_MODEL, adaptive thinking
except on claude-fable-*, refusal handling, cached system prompt, streaming +
get_final_message, usage logging). Not exercised by the offline test suite --
no network calls happen during `pytest`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import anthropic

from tourneydesk.prompts import SYSTEM_PROMPT
from tourneydesk.providers.base import AgentTurn, TextDelta
from tourneydesk.session import SpecSession
from tourneydesk.tools import TOOLS, dispatch

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 4096


class ClaudeIntake:
    """Drives a SpecSession by talking to Claude via the anthropic SDK."""

    def __init__(self, session: SpecSession, model: str | None = None) -> None:
        self.session = session
        self.model = model or os.environ.get("TOURNEYDESK_MODEL", DEFAULT_MODEL)
        self._client = anthropic.Anthropic()
        self._messages: list[dict[str, Any]] = []

    async def send(self, director_message: str, on_text_delta: TextDelta | None = None) -> AgentTurn:
        self._messages.append({"role": "user", "content": director_message})

        echoes: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        final_text = ""
        complete = False

        # Agentic loop: keep executing tool calls until Claude stops asking for them.
        while True:
            request_kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": _MAX_TOKENS,
                "system": [
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "tools": TOOLS,
                "messages": self._messages,
            }
            # claude-fable-* models run thinking always-on and reject an explicit
            # `thinking` param entirely -- omit it there, set it everywhere else.
            if not self.model.startswith("claude-fable"):
                request_kwargs["thinking"] = {"type": "adaptive"}

            with self._client.messages.stream(**request_kwargs) as stream:
                if on_text_delta is not None:
                    # Forward assistant text to the sink as it is generated so
                    # the web UI can render tokens live. `text_stream` yields
                    # only the natural-language text blocks (not tool JSON).
                    for chunk in stream.text_stream:
                        on_text_delta(chunk)
                response = stream.get_final_message()

            logger.info(
                "tourneydesk turn: model=%s stop_reason=%s input_tokens=%s output_tokens=%s",
                response.model,
                response.stop_reason,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )

            # Always check stop_reason before reading content -- a refusal can
            # carry an empty or partial content array.
            if response.stop_reason == "refusal":
                final_text = "Sorry, I wasn't able to respond to that. Could you rephrase?"
                break

            self._messages.append({"role": "assistant", "content": response.content})

            text_blocks = [block.text for block in response.content if block.type == "text"]
            if text_blocks:
                final_text = "\n".join(text_blocks)

            tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
            if not tool_use_blocks:
                break

            tool_results: list[dict[str, Any]] = []
            for block in tool_use_blocks:
                tool_calls.append({"name": block.name, "input": block.input})
                result = dispatch(self.session, block.name, block.input)
                echoes.append(result.content)
                if block.name == "mark_intake_complete" and not result.is_error:
                    complete = True
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )

            self._messages.append({"role": "user", "content": tool_results})

            if response.stop_reason != "tool_use":
                break

        return AgentTurn(text=final_text, tool_calls=tool_calls, echoes=echoes, complete=complete)
