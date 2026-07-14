"""Offline coverage for the Pydantic AI intake provider and the /chat endpoint.

No network, no keys: Pydantic AI's own ``FunctionModel``/``TestModel`` stand in
for a real model, so the whole agentic loop + the existing ``dispatch`` + the
``SpecSession`` all run exactly as they would live. This is the sanctioned way to
test a Pydantic AI agent (mirrors how ``FakeIntake`` gives the Anthropic path
offline coverage).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
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
