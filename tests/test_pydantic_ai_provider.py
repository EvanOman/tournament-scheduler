"""Offline coverage for the Pydantic AI intake provider and the /chat endpoint.

No network, no keys: Pydantic AI's own ``FunctionModel``/``TestModel`` stand in
for a real model, so the whole agentic loop + the existing ``dispatch`` + the
``SpecSession`` all run exactly as they would live. This is the sanctioned way to
test a Pydantic AI agent (mirrors how ``FakeIntake`` gives the Anthropic path
offline coverage).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from tourneydesk.providers.pydantic_ai import PydanticAIIntake
from tourneydesk.session import SpecSession

# --- scripted FunctionModel behaviours ------------------------------------


def _full_spec_model() -> FunctionModel:
    """A model that builds a complete, solvable draft, then replies with text."""
    calls = {"n": 0}

    def behaviour(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="add_division",
                        args={
                            "id": "d1",
                            "name": "Open",
                            "field_size": "full",
                            "game_duration_minutes": 40,
                            "games_per_team": 1,
                            "source_quote": "an Open division",
                        },
                    ),
                    ToolCallPart(
                        tool_name="set_team_count",
                        args={"division_id": "d1", "count": 4, "source_quote": "four teams"},
                    ),
                    ToolCallPart(
                        tool_name="add_field",
                        args={
                            "id": "f1",
                            "name": "Field 1",
                            "size": "full",
                            "availability": [{"start": "2027-06-12T09:00", "end": "2027-06-12T17:00"}],
                            "source_quote": "one field all Saturday",
                        },
                    ),
                ]
            )
        return ModelResponse(parts=[TextPart(content="All set — Open division, four teams, on Field 1.")])

    return FunctionModel(behaviour)


# --- provider loop --------------------------------------------------------


def test_loop_dispatches_tool_calls_and_returns_text() -> None:
    session = SpecSession()
    mutated: list[int] = []
    intake = PydanticAIIntake(session, models={"glm": _full_spec_model()})

    import asyncio

    turn = asyncio.run(intake.send("Open, four teams, one field.", on_spec_mutated=lambda: mutated.append(1)))

    assert turn.text.startswith("All set")
    assert {c["name"] for c in turn.tool_calls} == {"add_division", "set_team_count", "add_field"}
    assert "d1" in session.divisions
    assert len([t for t in session.teams.values() if t.division_id == "d1"]) == 4
    assert "f1" in session.fields
    assert turn.echoes  # successful mutations surfaced as provenance
    assert mutated  # on_spec_mutated fired per successful mutation
    assert intake._history  # history retained for the next turn


def test_no_key_is_friendly_and_offline(monkeypatch: Any) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    session = SpecSession()
    intake = PydanticAIIntake(session)  # no injected models, no keys -> no network

    import asyncio

    turn = asyncio.run(intake.send("hello", model_key="glm"))
    assert "isn't configured" in turn.text
    assert turn.tool_calls == []
    assert intake._history == []  # a no-key turn never touches history
    assert session.name == ""


def test_api_error_is_friendly() -> None:
    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("upstream 500")

    session = SpecSession()
    intake = PydanticAIIntake(session, models={"glm": FunctionModel(boom)})

    import asyncio

    turn = asyncio.run(intake.send("Open division"))
    assert "problem reaching the AI service" in turn.text
    assert intake._history == []  # failed turn doesn't corrupt history


def test_dispatch_errors_do_not_crash_the_loop() -> None:
    """A bad tool arg comes back as correctable error text, not a crash."""
    calls = {"n": 0}

    def behaviour(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            # Invalid field_size enum -> dispatch returns is_error text.
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="add_division",
                        args={
                            "id": "d1",
                            "name": "Open",
                            "field_size": "ginormous",
                            "game_duration_minutes": 40,
                            "source_quote": "q",
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="Let me fix that.")])

    session = SpecSession()
    intake = PydanticAIIntake(session, models={"glm": FunctionModel(behaviour)})

    import asyncio

    turn = asyncio.run(intake.send("add a division"))
    assert turn.text == "Let me fix that."
    assert "d1" not in session.divisions  # the bad call did not mutate state


def test_history_carries_across_model_switch() -> None:
    """First turn on GLM, second on GPT: the GPT run sees prior history."""
    session = SpecSession()
    seen_history_len: list[int] = []

    def gpt_behaviour(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen_history_len.append(len(messages))
        return ModelResponse(parts=[TextPart(content="Continuing on GPT.")])

    intake = PydanticAIIntake(
        session,
        models={"glm": _full_spec_model(), "gpt": FunctionModel(gpt_behaviour)},
    )

    import asyncio

    asyncio.run(intake.send("build it", model_key="glm"))
    turn2 = asyncio.run(intake.send("now on gpt", model_key="gpt"))

    assert turn2.text == "Continuing on GPT."
    # The GPT run received the accumulated conversation, not a fresh one.
    assert seen_history_len and seen_history_len[0] > 1


def test_testmodel_smoke_exercises_all_tools() -> None:
    """TestModel auto-calls every registered tool; the loop must survive it."""
    session = SpecSession()
    intake = PydanticAIIntake(session, models={"glm": TestModel()})

    import asyncio

    turn = asyncio.run(intake.send("anything"))
    assert isinstance(turn.text, str)  # no crash; all 19 tools dispatched


# --- streaming (on_text_delta / on_progress) -------------------------------
#
# `Agent.run_stream` calls a `FunctionModel`'s `stream_function` (not
# `function`) for EVERY graph iteration, including the tool-calling ones -- so
# unlike `_full_spec_model` above, this scripted model must speak in
# `DeltaToolCall`s for its first turn and plain `str` chunks for its second.

_REPLY_CHUNKS = ["All set", " — Open division,", " four teams,", " on Field 1."]


def _streaming_full_spec_model() -> FunctionModel:
    calls = {"n": 0}

    async def behaviour(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            yield {
                0: DeltaToolCall(
                    name="add_division",
                    json_args=json.dumps(
                        {
                            "id": "d1",
                            "name": "Open",
                            "field_size": "full",
                            "game_duration_minutes": 40,
                            "games_per_team": 1,
                            "source_quote": "an Open division",
                        }
                    ),
                ),
                1: DeltaToolCall(
                    name="set_team_count",
                    json_args=json.dumps({"division_id": "d1", "count": 4, "source_quote": "four teams"}),
                ),
                2: DeltaToolCall(
                    name="add_field",
                    json_args=json.dumps(
                        {
                            "id": "f1",
                            "name": "Field 1",
                            "size": "full",
                            "availability": [{"start": "2027-06-12T09:00", "end": "2027-06-12T17:00"}],
                            "source_quote": "one field all Saturday",
                        }
                    ),
                ),
            }
        else:
            for chunk in _REPLY_CHUNKS:
                yield chunk

    return FunctionModel(stream_function=behaviour)


def test_streaming_dispatches_tools_and_streams_true_deltas() -> None:
    session = SpecSession()
    deltas: list[str] = []
    progress: list[str] = []
    mutated: list[int] = []
    intake = PydanticAIIntake(session, models={"glm": _streaming_full_spec_model()})

    import asyncio

    turn = asyncio.run(
        intake.send(
            "Open, four teams, one field.",
            on_text_delta=deltas.append,
            on_spec_mutated=lambda: mutated.append(1),
            on_progress=progress.append,
        )
    )

    # Real token deltas: more than one chunk, and they concatenate to the text.
    assert len(deltas) > 1
    assert "".join(deltas) == turn.text
    assert turn.text.startswith("All set")

    # Tool calls still dispatched through the same `deps`/`dispatch` path.
    assert {c["name"] for c in turn.tool_calls} == {"add_division", "set_team_count", "add_field"}
    assert "d1" in session.divisions
    assert len([t for t in session.teams.values() if t.division_id == "d1"]) == 4
    assert "f1" in session.fields

    # `on_progress` fires with the same dispatch echoes `on_spec_mutated` fires for.
    assert progress == turn.echoes
    assert progress  # non-empty: at least one successful mutation
    assert mutated

    # History persisted for the next turn (either engine).
    assert intake._history


def test_streaming_history_persists_across_turns() -> None:
    """A second streamed turn sees the first turn's history."""
    session = SpecSession()
    seen_history_len: list[int] = []

    async def second_turn_behaviour(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        seen_history_len.append(len(messages))
        yield "Continuing the conversation."

    intake = PydanticAIIntake(
        session,
        models={"glm": _streaming_full_spec_model(), "gpt": FunctionModel(stream_function=second_turn_behaviour)},
    )

    import asyncio

    deltas1: list[str] = []
    asyncio.run(intake.send("build it", on_text_delta=deltas1.append, model_key="glm"))

    deltas2: list[str] = []
    turn2 = asyncio.run(intake.send("now on gpt", on_text_delta=deltas2.append, model_key="gpt"))

    assert turn2.text == "Continuing the conversation."
    assert seen_history_len and seen_history_len[0] > 1  # saw the accumulated conversation


def test_streaming_api_error_is_friendly() -> None:
    async def boom_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if False:  # pragma: no cover -- keeps this an async generator function
            yield ""
        raise RuntimeError("upstream 500")

    session = SpecSession()
    intake = PydanticAIIntake(session, models={"glm": FunctionModel(stream_function=boom_stream)})

    import asyncio

    deltas: list[str] = []
    turn = asyncio.run(intake.send("Open division", on_text_delta=deltas.append))

    assert "problem reaching the AI service" in turn.text
    assert deltas and "problem reaching the AI service" in deltas[0]
    assert intake._history == []  # failed streamed turn doesn't corrupt history


# --- /chat endpoint -------------------------------------------------------


def _seed_store(session_id: str, models: dict[str, Any]) -> None:
    from demo.api.main import _CHAT_STORE

    intake = PydanticAIIntake(SpecSession(), models=models)
    _CHAT_STORE._items[session_id] = (intake, time.monotonic())


def test_chat_endpoint_returns_reply_rules_and_schedule() -> None:
    from demo.api.main import app

    _seed_store("sess-pa", {"glm": _full_spec_model()})
    client = TestClient(app)

    resp = client.post("/chat", json={"session_id": "sess-pa", "message": "build it", "model": "glm"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["session_id"] == "sess-pa"
    assert body["reply"].startswith("All set")
    assert [d["id"] for d in body["rules"]["divisions"]] == ["d1"]
    assert len(body["rules"]["teams"]) == 4
    assert body["schedule"]["status"] == "solved"
    assert body["schedule"]["stats"]["num_games_scheduled"] >= 1


def test_chat_endpoint_mints_session_and_defaults_to_glm(monkeypatch: Any) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from demo.api.main import app

    client = TestClient(app)
    resp = client.post("/chat", json={"message": "hi there"})  # no model -> defaults to glm
    assert resp.status_code == 200
    body = resp.json()

    assert body["session_id"]
    assert "isn't configured" in body["reply"]
    assert body["schedule"]["status"] == "incomplete"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
