"""Live intake provider built on Pydantic AI, with two switchable engines.

Pydantic AI runs the agentic tool loop for us, so there is no hand-rolled
call/dispatch/feed-back loop here (contrast ``ClaudeIntake``). We register the
existing 19 ``TOOLS`` from their explicit JSON schemas via
``Tool.from_schema(..., takes_ctx=True)`` and route every call to
``dispatch(session, name, args)`` UNCHANGED, and we reuse ``SYSTEM_PROMPT``
verbatim as the agent instructions. That keeps the demo's conversation path the
product's path -- same tools, same dispatch, same ``SpecSession``.

Two engines are exposed and selectable per message; conversation history is
carried across a mid-chat switch (Pydantic AI message history in -> continue on
the other model):

  * "glm" -> Z.AI GLM over its OpenAI-compatible chat API
            (ZAI_API_KEY, GLM_MODEL default "glm-5.2", ZAI_BASE_URL).
  * "gpt" -> native OpenAI chat (OPENAI_API_KEY, GPT_MODEL).

Both models are capped at MAX_OUTPUT_TOKENS output per turn so no single reply
can run away in cost. A missing key for the requested engine yields a friendly,
no-network ``AgentTurn`` (and never pollutes history). The offline test suite
injects Pydantic AI ``FunctionModel``/``TestModel`` instead of a real model, so
the whole loop runs with no key and no sockets.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from tourneydesk.prompts import SYSTEM_PROMPT
from tourneydesk.providers.base import AgentTurn, SpecMutated, TextDelta
from tourneydesk.session import SpecSession
from tourneydesk.tools import TOOLS, dispatch

logger = logging.getLogger(__name__)

DEFAULT_GLM_MODEL = "glm-5.2"
DEFAULT_GPT_MODEL = "gpt-5.6-sol"
DEFAULT_ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"
DEFAULT_MAX_OUTPUT_TOKENS = 800
_PUBLIC_DEMO_MAX_TURNS = 12
_PUBLIC_DEMO_MAX_HISTORY_BYTES = 64 * 1024

# Read-only tools whose results are internal dumps, not user-facing provenance.
_READONLY_TOOLS = ("get_spec_summary", "get_schedule_summary")

_FRIENDLY_NO_KEY = (
    "The {engine} engine isn't configured with a key right now, so I can't chat on it just yet. "
    "Try the other model in the switcher, or use the constraint controls below."
)
_FRIENDLY_API_ERROR = (
    "Sorry, I hit a problem reaching the AI service just now. Your draft is safe — "
    "please try sending that again in a moment."
)
_FRIENDLY_DEMO_LIMIT = (
    "You've reached this session's demo chat limit. Start a new draft to keep chatting, "
    "or use the constraint controls below."
)


@dataclass
class _Deps:
    """Per-run collectors handed to every tool via ``ctx.deps``."""

    session: SpecSession
    on_spec_mutated: SpecMutated | None = None
    on_progress: Callable[[str], None] | None = None
    echoes: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = False


def _make_tool_fn(name: str) -> Any:
    """Build the callable Pydantic AI invokes for tool ``name``.

    ``from_schema`` calls this as ``fn(ctx, **validated_kwargs)``. We forward the
    kwargs dict straight to the existing ``dispatch`` and return its plain-text
    result (success echo OR correctable error message) for the model to read --
    exactly the contract ``dispatch`` was built for.
    """

    def fn(ctx: RunContext[_Deps], **kwargs: Any) -> str:
        deps = ctx.deps
        result = dispatch(deps.session, name, kwargs)
        deps.tool_calls.append({"name": name, "input": kwargs})
        # Echo successful MUTATIONS only; errors are model-facing corrections and
        # read-only summaries are internal dumps (mirrors the Anthropic path).
        if not result.is_error and name not in _READONLY_TOOLS:
            deps.echoes.append(result.content)
            if deps.on_spec_mutated is not None:
                deps.on_spec_mutated()
            if deps.on_progress is not None:
                deps.on_progress(result.content)
        if name == "mark_intake_complete" and not result.is_error:
            deps.complete = True
        return result.content

    return fn


def _build_tools() -> list[Tool[_Deps]]:
    return [
        Tool.from_schema(
            _make_tool_fn(t["name"]),
            name=t["name"],
            description=t["description"],
            json_schema=t["input_schema"],
            takes_ctx=True,
            # Every tool reads or mutates the same SpecSession. Providers may
            # return dependent calls (add division, then add its teams) in one
            # response, so concurrent dispatch would race and intermittently
            # drop a valid later mutation.
            sequential=True,
        )
        for t in TOOLS
    ]


# One agent, built once: tools + instructions are fixed; the model is chosen per
# run so a single agent serves both engines.
_AGENT: Agent[_Deps, str] = Agent(
    deps_type=_Deps,
    tools=_build_tools(),
    instructions=SYSTEM_PROMPT,
)


def _max_output_tokens() -> int:
    try:
        return int(os.environ.get("MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS))
    except ValueError:
        return DEFAULT_MAX_OUTPUT_TOKENS


def _build_model(engine: str) -> Model | None:
    """Construct the Pydantic AI model for an engine, or None if unconfigured."""
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings  # noqa: PLC0415 -- lazy import
    from pydantic_ai.providers.openai import OpenAIProvider  # noqa: PLC0415

    max_tokens = _max_output_tokens()
    if engine == "glm":
        key = os.environ.get("ZAI_API_KEY")
        if not key:
            return None
        base_url = os.environ.get("ZAI_BASE_URL", DEFAULT_ZAI_BASE_URL)
        model_name = os.environ.get("GLM_MODEL", DEFAULT_GLM_MODEL)
        settings = OpenAIChatModelSettings(max_tokens=max_tokens)
        return OpenAIChatModel(model_name, provider=OpenAIProvider(base_url=base_url, api_key=key), settings=settings)
    if engine == "gpt":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return None
        model_name = os.environ.get("GPT_MODEL", DEFAULT_GPT_MODEL)
        # GPT-5.x are reasoning models: the chat-completions endpoint REJECTS
        # function tools unless reasoning_effort is 'none' (the API's own guidance
        # in the 400 it returns otherwise). 'none' also keeps the 800-token output
        # cap meaningful — no hidden reasoning tokens eat the budget.
        settings = OpenAIChatModelSettings(max_tokens=max_tokens, openai_reasoning_effort="none")
        return OpenAIChatModel(model_name, provider=OpenAIProvider(api_key=key), settings=settings)
    return None


def _price(name: str) -> float | None:
    """Read a non-negative per-million-token price, or leave cost unknown."""
    try:
        value = float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _model_name(model: Model) -> str | None:
    """Read model identity without letting an observational property fail a turn."""
    try:
        value = getattr(model, "model_name", None)
    except Exception:  # noqa: BLE001 -- telemetry is always fail-open
        return None
    return str(value) if value is not None else None


def _usage_fields(engine: str, model: Model, result: Any) -> dict[str, Any]:
    """Best-effort model, token counts, and configured cost for a completed run.

    Purely observational: Pydantic AI's usage surface has moved between releases,
    so a missing attribute yields empty telemetry rather than a failed turn the
    user would actually notice.
    """
    fields: dict[str, Any] = {}
    try:
        fields["model_name"] = _model_name(model)
        usage = result.usage()
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        fields["input_tokens"] = input_tokens
        fields["output_tokens"] = output_tokens
        input_price = _price(f"{engine.upper()}_INPUT_USD_PER_MILLION_TOKENS")
        output_price = _price(f"{engine.upper()}_OUTPUT_USD_PER_MILLION_TOKENS")
        if (
            isinstance(input_tokens, int)
            and isinstance(output_tokens, int)
            and input_price is not None
            and output_price is not None
        ):
            fields["estimated_cost_usd"] = round(
                (input_tokens * input_price + output_tokens * output_price) / 1_000_000,
                9,
            )
    except Exception:  # noqa: BLE001 -- telemetry never breaks a reply
        logger.debug("usage unavailable for this turn", exc_info=True)
    return fields


class PydanticAIIntake:
    """Drives a SpecSession via Pydantic AI, on either the GLM or GPT engine.

    One instance owns one conversation: a ``SpecSession`` plus the Pydantic AI
    message history. The engine is chosen per ``send`` and history is shared, so
    switching models mid-chat continues the same conversation on the other model.

    ``models`` is injectable so tests can supply ``FunctionModel``/``TestModel``
    keyed by engine ("glm"/"gpt") and never touch the network.

    ``public_demo`` bounds each model loop and retains at most 64 KiB of whole
    conversation history. A session allows twelve model-started turns, including
    failures, and closes after a usage limit or history overflow. CLI and product
    callers keep their existing policy when this option is omitted.
    """

    def __init__(
        self, session: SpecSession, models: dict[str, Model] | None = None, *, public_demo: bool = False
    ) -> None:
        self.session = session
        self._history: list[Any] = []
        self._public_demo = public_demo
        self._demo_turns = 0
        self._demo_closed = False
        # Cache built (or injected) models per engine; None means unconfigured.
        self._models: dict[str, Model | None] = dict(models) if models else {}

    def _remember_history(self, history: list[Any]) -> None:
        """Retain whole runs only; never split a tool call from its response."""
        if self._public_demo and len(ModelMessagesTypeAdapter.dump_json(history)) > _PUBLIC_DEMO_MAX_HISTORY_BYTES:
            self._demo_closed = True
            return
        self._history = history

    def _demo_limit_turn(self, on_text_delta: TextDelta | None, deps: _Deps | None = None) -> AgentTurn:
        _stream(_FRIENDLY_DEMO_LIMIT, on_text_delta)
        return AgentTurn(
            text=_FRIENDLY_DEMO_LIMIT,
            tool_calls=deps.tool_calls if deps else [],
            echoes=deps.echoes if deps else [],
            complete=False,
        )

    def _model_for(self, engine: str) -> Model | None:
        if engine not in self._models:
            self._models[engine] = _build_model(engine)
        return self._models[engine]

    async def send(
        self,
        director_message: str,
        on_text_delta: TextDelta | None = None,
        on_spec_mutated: SpecMutated | None = None,
        *,
        model_key: str = "glm",
        on_progress: Callable[[str], None] | None = None,
    ) -> AgentTurn:
        if self._public_demo and (self._demo_closed or self._demo_turns >= _PUBLIC_DEMO_MAX_TURNS):
            return self._demo_limit_turn(on_text_delta)
        engine = model_key if model_key in ("glm", "gpt") else "glm"
        model = self._model_for(engine)
        if model is None:
            text = _FRIENDLY_NO_KEY.format(engine=engine.upper())
            _stream(text, on_text_delta)
            return AgentTurn(text=text, tool_calls=[], echoes=[], complete=False)

        # Count attempts, including interrupted or failed runs. Otherwise a
        # repeatedly failing tool loop could evade the session work ceiling.
        if self._public_demo:
            self._demo_turns += 1
        usage_limits = (
            UsageLimits(request_limit=4, tool_calls_limit=16, input_tokens_limit=16000, output_tokens_limit=3200)
            if self._public_demo
            else None
        )
        model_settings: ModelSettings | None = {"max_tokens": DEFAULT_MAX_OUTPUT_TOKENS} if self._public_demo else None
        deps = _Deps(session=self.session, on_spec_mutated=on_spec_mutated, on_progress=on_progress)

        if on_text_delta is None:
            # The demo's plain-request /chat endpoint uses the non-streaming path.
            try:
                result = await _AGENT.run(
                    director_message,
                    model=model,
                    message_history=self._history or None,
                    deps=deps,
                    usage_limits=usage_limits,
                    model_settings=model_settings,
                )
            except Exception as exc:  # noqa: BLE001 -- model/transport errors must produce a user-facing reply
                if self._public_demo and isinstance(exc, UsageLimitExceeded):
                    self._demo_closed = True
                    return self._demo_limit_turn(on_text_delta, deps)
                logger.exception("Pydantic AI run failed on engine=%s", engine)
                text = _FRIENDLY_API_ERROR
                _stream(text, on_text_delta)
                return AgentTurn(
                    text=text,
                    tool_calls=deps.tool_calls,
                    echoes=deps.echoes,
                    complete=False,
                    model_name=_model_name(model),
                    provider_error=True,
                )

            # Persist the full history (incl. this turn) so the NEXT turn -- on
            # either engine -- continues the same conversation.
            self._remember_history(list(result.all_messages()))
            final_text = result.output if isinstance(result.output, str) else str(result.output)
            _stream(final_text, on_text_delta)
            return AgentTurn(
                text=final_text,
                tool_calls=deps.tool_calls,
                echoes=deps.echoes,
                complete=deps.complete,
                **_usage_fields(engine, model, result),
            )

        # A sink was given -> stream real token deltas of the FINAL assistant
        # text via `run_stream` + `stream_text(delta=True)`. `run_stream` runs
        # the full agent graph -- all tool calls dispatch exactly as in the
        # non-streaming path above, through the same `deps` -- up to the first
        # text output (see its docstring), so this mirrors `run()` except for
        # how the final text arrives.
        try:
            async with _AGENT.run_stream(
                director_message,
                model=model,
                message_history=self._history or None,
                deps=deps,
                usage_limits=usage_limits,
                model_settings=model_settings,
            ) as result:
                async for chunk in result.stream_text(delta=True, debounce_by=None):
                    if chunk:
                        on_text_delta(chunk)
                final_output = await result.get_output()
                history = list(result.all_messages())
                usage_fields = _usage_fields(engine, model, result)
        except Exception as exc:  # noqa: BLE001 -- model/transport errors must produce a user-facing reply
            if self._public_demo and isinstance(exc, UsageLimitExceeded):
                self._demo_closed = True
                return self._demo_limit_turn(on_text_delta, deps)
            logger.exception("Pydantic AI run_stream failed on engine=%s", engine)
            text = _FRIENDLY_API_ERROR
            _stream(text, on_text_delta)
            return AgentTurn(
                text=text,
                tool_calls=deps.tool_calls,
                echoes=deps.echoes,
                complete=False,
                model_name=_model_name(model),
                provider_error=True,
            )

        self._remember_history(history)
        final_text = final_output if isinstance(final_output, str) else str(final_output)
        return AgentTurn(
            text=final_text,
            tool_calls=deps.tool_calls,
            echoes=deps.echoes,
            complete=deps.complete,
            **usage_fields,
        )


def _stream(text: str, on_text_delta: TextDelta | None) -> None:
    """Push one whole-text chunk to a streaming sink, if one was provided.

    Used for the no-key and API-error fallbacks on BOTH the plain and
    streaming paths of ``send`` -- those replies are short, fixed strings with
    nothing to meaningfully token-stream, so they go out as a single chunk even
    when a sink is present. The `/chat` endpoint passes no sink, so this is a
    no-op there. True token deltas for a real reply go through
    ``Agent.run_stream`` in ``send`` itself, not through this helper.
    """
    if on_text_delta is None or not text:
        return
    on_text_delta(text)
