"""Subscription-billed intake provider via the Claude Agent SDK.

Drives the same SpecSession + tool suite as ClaudeIntake, but through the
Claude Agent SDK — which spawns a (bundled) Claude Code binary authenticated
by the machine's Claude Code OAuth login, so turns bill the owner's Max
subscription instead of API credits (DECISIONS D29).

POLICY BOUNDARY (do not remove): subscription auth is acceptable ONLY for the
account owner's personal/dev use on their own machine. Anthropic does not
allow offering claude.ai login or rate limits to third-party users of a
product, including Agent SDK agents. Anything user-facing beyond the owner
(the concierge business, demos for design partners driving it themselves)
must run ClaudeIntake (API billing): set TOURNEYDESK_PROVIDER=api.

Threading model: the SDK client is async and must live on ONE event loop for
the life of the conversation, but the web layer runs each turn in a fresh
`asyncio.run` inside a worker thread. Each provider instance therefore owns a
dedicated daemon thread running a persistent loop; `send()` proxies onto it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool
from claude_agent_sdk.types import StreamEvent

from tourneydesk.prompts import SYSTEM_PROMPT
from tourneydesk.providers.base import AgentTurn, SpecMutated, TextDelta
from tourneydesk.session import SpecSession
from tourneydesk.tools import TOOLS, dispatch

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"
_READ_ONLY_TOOLS = ("get_spec_summary", "get_schedule_summary")


class AgentSDKIntake:
    """Drives a SpecSession via the Claude Agent SDK (subscription auth)."""

    def __init__(self, session: SpecSession, model: str | None = None) -> None:
        self.session = session
        self.model = model or os.environ.get("TOURNEYDESK_MODEL", DEFAULT_MODEL)
        self._client: ClaudeSDKClient | None = None
        # Per-turn state written by tool handlers running on the SDK loop.
        self._echoes: list[str] = []
        self._tool_calls: list[dict[str, Any]] = []
        self._complete = False
        self._on_text_delta: TextDelta | None = None
        self._on_spec_mutated: SpecMutated | None = None

        # The SDK subprocess resolves credentials with ANTHROPIC_API_KEY ahead
        # of the Claude Code OAuth login, so an inherited key silently flips
        # billing back to API credits. Scrub it from this process (Python's
        # ClaudeAgentOptions.env MERGES onto the inherited env, so popping the
        # parent env is the only reliable scrub).
        os.environ.pop("ANTHROPIC_API_KEY", None)

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="agent-sdk-loop", daemon=True)
        self._thread.start()

    # -- provider contract ---------------------------------------------------

    async def send(
        self,
        director_message: str,
        on_text_delta: TextDelta | None = None,
        on_spec_mutated: SpecMutated | None = None,
    ) -> AgentTurn:
        future = asyncio.run_coroutine_threadsafe(
            self._turn(director_message, on_text_delta, on_spec_mutated), self._loop
        )
        return await asyncio.wrap_future(future)

    # -- internals (run on the dedicated SDK loop) ----------------------------

    async def _turn(self, text: str, on_text_delta: TextDelta | None, on_spec_mutated: SpecMutated | None) -> AgentTurn:
        if self._client is None:
            self._client = ClaudeSDKClient(options=self._options())
            await self._client.connect()

        self._echoes, self._tool_calls = [], []
        self._on_text_delta, self._on_spec_mutated = on_text_delta, on_spec_mutated
        final_text_parts: list[str] = []

        await self._client.query(text)
        async for message in self._client.receive_response():
            if isinstance(message, StreamEvent):
                event = message.event
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta" and on_text_delta is not None:
                        on_text_delta(delta.get("text", ""))
                continue
            content = getattr(message, "content", None)
            if content:
                for block in content:
                    block_text = getattr(block, "text", None)
                    if block_text:
                        final_text_parts.append(block_text)

        return AgentTurn(
            text="\n".join(final_text_parts),
            tool_calls=self._tool_calls,
            echoes=self._echoes,
            complete=self._complete,
        )

    def _options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            model=self.model,
            tools=[],  # no built-ins (file/bash/web) — spec tools only
            mcp_servers={"spec": create_sdk_mcp_server(name="spec", version="1.0.0", tools=self._sdk_tools())},
            allowed_tools=["mcp__spec__*"],
            setting_sources=[],  # never leak local CLAUDE.md / settings into product turns
            include_partial_messages=True,
        )

    def _sdk_tools(self) -> list[Any]:
        sdk_tools = []
        for spec_tool in TOOLS:

            def make_handler(tool_name: str):
                async def handler(args: dict[str, Any]) -> dict[str, Any]:
                    result = dispatch(self.session, tool_name, args)
                    self._tool_calls.append({"name": tool_name, "input": args})
                    if not result.is_error and tool_name not in _READ_ONLY_TOOLS:
                        self._echoes.append(result.content)
                        if self._on_spec_mutated is not None:
                            self._on_spec_mutated()
                    if tool_name == "mark_intake_complete" and not result.is_error:
                        self._complete = True
                    return {"content": [{"type": "text", "text": result.content}], "is_error": result.is_error}

                return handler

            sdk_tools.append(
                tool(spec_tool["name"], spec_tool["description"], spec_tool["input_schema"])(
                    make_handler(spec_tool["name"])
                )
            )
        return sdk_tools
