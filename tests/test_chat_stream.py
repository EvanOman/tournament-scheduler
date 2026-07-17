"""Offline coverage for the `/chat/stream` SSE endpoint.

No network, no keys: same `FunctionModel` injection pattern as
`test_pydantic_ai_provider.py`'s `/chat` coverage, reused here via
`_streaming_full_spec_model`/`_seed_store` so the streaming scripted model
exercises the real `dispatch()` + `SpecSession` + CP-SAT solve path.

Asserts the frozen SSE contract (docs/DECISIONS.md D30): zero or more `status`
frames, then zero or more `delta` frames, then exactly one terminal `final` (or
`error`) frame, each frame `"event: <name>\\ndata: <single-line JSON>\\n\\n"`.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_pydantic_ai_provider import _seed_store, _streaming_full_spec_model
from tourneydesk.providers.pydantic_ai import PydanticAIIntake
from tourneydesk.session import SpecSession


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        lines = frame.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        assert "\n" not in data  # single-line JSON per the contract
        events.append((event, json.loads(data)))
    return events


def test_chat_stream_emits_status_then_delta_then_final() -> None:
    from demo.api.main import app

    _seed_store("sess-stream", {"glm": _streaming_full_spec_model()})
    client = TestClient(app)

    resp = client.post("/chat/stream", json={"session_id": "sess-stream", "message": "build it", "model": "glm"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"

    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]

    # 3 mutation echoes (add_division, set_team_count, add_field), then >=1 true
    # token deltas, then the pre-solve "Solving..." status, then exactly one
    # terminal `final` -- last, no `error`.
    assert kinds[:3] == ["status", "status", "status"]
    assert kinds[-2:] == ["status", "final"]
    delta_kinds = kinds[3:-2]
    assert delta_kinds and set(delta_kinds) == {"delta"}
    assert "error" not in kinds

    mutation_texts = [data["text"] for kind, data in events[:3]]
    assert all(mutation_texts)  # each is the dispatch echo, non-empty
    assert events[-2][1]["text"] == "Solving your schedule…"

    reply = "".join(data["text"] for kind, data in events[3:-2] if kind == "delta")
    final_event, final_data = events[-1]
    assert final_event == "final"
    assert final_data["session_id"] == "sess-stream"
    assert final_data["reply"] == reply
    assert final_data["reply"].startswith("All set")
    assert [d["id"] for d in final_data["rules"]["divisions"]] == ["d1"]
    assert len(final_data["rules"]["teams"]) == 4
    assert final_data["schedule"]["status"] == "solved"
    assert final_data["schedule"]["stats"]["num_games_scheduled"] >= 1


def test_chat_stream_mints_session_and_defaults_to_glm(monkeypatch: Any) -> None:
    """No key configured -> a single friendly delta, no mutation status, still
    ends in `final` with an incomplete-schedule fallback (mirrors /chat)."""
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from demo.api.main import app

    client = TestClient(app)
    resp = client.post("/chat/stream", json={"message": "hi there"})  # no model -> defaults to glm

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]

    assert kinds == ["delta", "status", "final"]
    assert "isn't configured" in events[0][1]["text"]
    assert events[1][1]["text"] == "Solving your schedule…"
    final_data = events[2][1]
    assert final_data["session_id"]  # a fresh session_id was minted
    assert "isn't configured" in final_data["reply"]
    assert final_data["schedule"]["status"] == "incomplete"


def test_chat_stream_error_path_ends_with_error_event() -> None:
    """A bug that escapes the provider entirely (not just a model/API error,
    which the provider already turns into a friendly reply) must still close
    the stream cleanly with a terminal `error` frame, not a hang or a 500."""
    from demo.api.main import _CHAT_STORE, app

    session_id = "sess-stream-boom"
    intake = PydanticAIIntake(SpecSession(), models={"glm": _streaming_full_spec_model()})

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("unexpected bug")

    intake.send = _boom  # type: ignore[method-assign]
    _CHAT_STORE._items[session_id] = (intake, time.monotonic())

    client = TestClient(app)
    resp = client.post("/chat/stream", json={"session_id": session_id, "message": "build it", "model": "glm"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)

    assert len(events) == 1
    event, data = events[0]
    assert event == "error"
    assert data["message"]
    assert "final" not in [e for e, _ in events]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
