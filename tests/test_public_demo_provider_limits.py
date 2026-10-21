"""The public intake has finite offline-tested model and session budgets."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

from tourneydesk.providers.pydantic_ai import PydanticAIIntake
from tourneydesk.session import SpecSession


@pytest.mark.parametrize("streaming", [False, True])
def test_public_demo_stops_a_runaway_tool_loop_and_closes_session(streaming: bool, monkeypatch: Any) -> None:
    requests: list[int] = []

    def loop(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        requests.append(1)
        return ModelResponse(parts=[ToolCallPart(tool_name="get_spec_summary", args={})])

    async def stream_loop(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        requests.append(1)
        yield {0: DeltaToolCall(name="get_spec_summary", json_args="{}")}

    intake = PydanticAIIntake(
        SpecSession(),
        models={"glm": FunctionModel(loop, stream_function=stream_loop)},
        public_demo=True,
    )
    deltas: list[str] = []
    turn = asyncio.run(intake.send("summarize", on_text_delta=deltas.append if streaming else None))

    assert len(requests) == 4
    assert "demo chat limit" in turn.text
    assert not intake._history
    if streaming:
        assert "".join(deltas) == turn.text

    def no_provider(engine: str) -> None:
        pytest.fail("an exhausted session must reject before provider construction")

    monkeypatch.setattr(intake, "_model_for", no_provider)
    again = asyncio.run(intake.send("try again"))
    assert "demo chat limit" in again.text
    assert len(requests) == 4


@pytest.mark.parametrize("streaming", [False, True])
def test_public_demo_caps_tools_in_one_model_response(streaming: bool) -> None:
    calls = [ToolCallPart(tool_name="get_spec_summary", args={}) for _ in range(17)]

    def tools(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=calls)

    async def stream_tools(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        yield {i: DeltaToolCall(name="get_spec_summary", json_args="{}") for i in range(17)}

    intake = PydanticAIIntake(
        SpecSession(),
        models={"glm": FunctionModel(tools, stream_function=stream_tools)},
        public_demo=True,
    )
    turn = asyncio.run(intake.send("summarize", on_text_delta=(lambda _: None) if streaming else None))

    assert "demo chat limit" in turn.text
    assert len(turn.tool_calls) <= 16
    assert not intake._history


@pytest.mark.parametrize("streaming", [False, True])
def test_public_demo_overrides_large_output_environment_limit(streaming: bool, monkeypatch: Any) -> None:
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "1000000")
    settings: list[Any] = []

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        settings.append(info.model_settings)
        return ModelResponse(parts=[TextPart(content="Ready.")])

    async def stream_answer(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        settings.append(info.model_settings)
        yield "Ready."

    intake = PydanticAIIntake(
        SpecSession(),
        models={"glm": FunctionModel(answer, stream_function=stream_answer)},
        public_demo=True,
    )
    turn = asyncio.run(intake.send("hello", on_text_delta=(lambda _: None) if streaming else None))

    assert turn.text == "Ready."
    assert settings == [{"max_tokens": 800}]


@pytest.mark.parametrize(("input_tokens", "output_tokens"), [(16001, 0), (0, 3201)])
def test_public_demo_checks_reported_token_usage(input_tokens: int, output_tokens: int) -> None:
    def over_budget(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="Ready.")],
            usage=RequestUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    intake = PydanticAIIntake(SpecSession(), models={"glm": FunctionModel(over_budget)}, public_demo=True)
    turn = asyncio.run(intake.send("hello"))

    assert "demo chat limit" in turn.text
    assert not intake._history


def test_public_demo_checks_streamed_output_usage() -> None:
    async def over_budget(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        yield "hello " * 4000

    intake = PydanticAIIntake(
        SpecSession(), models={"glm": FunctionModel(stream_function=over_budget)}, public_demo=True
    )
    turn = asyncio.run(intake.send("hello", on_text_delta=lambda _: None))

    assert "demo chat limit" in turn.text
    assert not intake._history


def test_failed_model_runs_still_exhaust_the_public_session(monkeypatch: Any) -> None:
    calls: list[int] = []

    def unavailable(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        raise RuntimeError("offline simulated outage")

    intake = PydanticAIIntake(SpecSession(), models={"glm": FunctionModel(unavailable)}, public_demo=True)
    for _ in range(12):
        turn = asyncio.run(intake.send("hello"))
        assert "problem reaching the AI service" in turn.text

    def no_provider(engine: str) -> None:
        pytest.fail("the attempt budget must be checked before provider construction")

    monkeypatch.setattr(intake, "_model_for", no_provider)
    assert "demo chat limit" in asyncio.run(intake.send("retry")).text
    assert len(calls) == 12
    assert not intake._history


@pytest.mark.parametrize("streaming", [False, True])
def test_successful_public_session_allows_twelve_turns(streaming: bool, monkeypatch: Any) -> None:
    calls: list[int] = []

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[TextPart(content="Ready.")])

    async def stream_answer(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        calls.append(1)
        yield "Ready."

    intake = PydanticAIIntake(
        SpecSession(), models={"glm": FunctionModel(answer, stream_function=stream_answer)}, public_demo=True
    )
    for _ in range(12):
        assert asyncio.run(intake.send("hello", on_text_delta=(lambda _: None) if streaming else None)).text == "Ready."

    def no_provider(engine: str) -> None:
        pytest.fail("the turn cap must be checked before provider construction")

    monkeypatch.setattr(intake, "_model_for", no_provider)
    assert "demo chat limit" in asyncio.run(intake.send("another")).text
    assert len(calls) == 12


def test_oversized_new_history_keeps_complete_previous_history_and_closes_session(monkeypatch: Any) -> None:
    # A model that ignores its output ceiling still cannot grow retained history.
    # Explicit usage keeps this test focused on the independent byte ceiling.
    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if isinstance(messages[-1], ModelRequest) and not any(
            isinstance(part, ToolReturnPart) for part in messages[-1].parts
        ):
            return ModelResponse(
                parts=[ToolCallPart(tool_name="get_spec_summary", args={}, tool_call_id=f"summary-{len(messages)}")],
                usage=RequestUsage(input_tokens=1, output_tokens=1),
            )
        return ModelResponse(parts=[TextPart(content="x" * 40000)], usage=RequestUsage(input_tokens=1, output_tokens=1))

    intake = PydanticAIIntake(SpecSession(), models={"glm": FunctionModel(answer)}, public_demo=True)
    asyncio.run(intake.send("first"))
    previous = ModelMessagesTypeAdapter.dump_json(intake._history)
    assert 40000 < len(previous) < 65536
    tool_ids = [
        part.tool_call_id for message in intake._history for part in message.parts if isinstance(part, ToolCallPart)
    ]
    returned_ids = [
        part.tool_call_id for message in intake._history for part in message.parts if isinstance(part, ToolReturnPart)
    ]
    assert tool_ids == returned_ids
    assert len(tool_ids) == 1

    asyncio.run(intake.send("second"))
    assert ModelMessagesTypeAdapter.dump_json(intake._history) == previous

    def no_provider(engine: str) -> None:
        pytest.fail("history exhaustion must reject before provider construction")

    monkeypatch.setattr(intake, "_model_for", no_provider)
    assert "demo chat limit" in asyncio.run(intake.send("third")).text


def test_full_product_keeps_its_existing_longer_tool_loop() -> None:
    calls: list[int] = []

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        if len(calls) < 6:
            return ModelResponse(parts=[ToolCallPart(tool_name="get_spec_summary", args={})])
        return ModelResponse(parts=[TextPart(content="Ready.")])

    intake = PydanticAIIntake(SpecSession(), models={"glm": FunctionModel(answer)})
    assert asyncio.run(intake.send("hello")).text == "Ready."
    assert len(calls) == 6
    assert intake._history
