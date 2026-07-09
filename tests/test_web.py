"""Web layer end-to-end over the FakeIntake provider -- zero network, zero key.

Proves the vertical slice the browser depends on: REST session lifecycle, then a
full WebSocket conversation (streaming deltas -> spec_updated -> debounced
speculative solve -> solve_completed carrying a real, valid schedule). Uses the
same IntakeService path the CLI drives, so a green run here means the browser is
exercising validated wiring.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tourneydesk.web.app import create_app, fake_factory
from tourneydesk.web.canned import CANNED_SCRIPT


def _client() -> TestClient:
    app = create_app(db_path=":memory:", provider_factory=fake_factory, debounce_seconds=0.05)
    return TestClient(app)


def test_rest_session_lifecycle() -> None:
    client = _client()

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/sessions").json() == []

    created = client.post("/api/sessions", json={"title": "My Cup"}).json()
    sid = created["id"]
    assert created["title"] == "My Cup"

    listing = client.get("/api/sessions").json()
    assert len(listing) == 1 and listing[0]["id"] == sid

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["id"] == sid
    assert "rules" in detail and "transcript" in detail

    # Nothing captured yet -> spec incomplete, schedule waiting.
    spec = client.get(f"/api/sessions/{sid}/spec").json()
    assert spec["complete"] is False and spec["missing"]

    sched = client.get(f"/api/sessions/{sid}/schedule").json()
    assert sched["status"] == "incomplete"

    assert client.get("/api/sessions/does-not-exist").status_code == 404


def _drain_until(ws: Any, events: list[dict[str, Any]], predicate: Any, budget: int = 80) -> dict[str, Any] | None:
    for _ in range(budget):
        ev = ws.receive_json()
        events.append(ev)
        if predicate(ev):
            return ev
    return None


def test_ws_full_conversation_streams_and_solves() -> None:
    client = _client()
    sid = client.post("/api/sessions").json()["id"]
    lines = [f"message {i}" for i in range(len(CANNED_SCRIPT))]
    events: list[dict[str, Any]] = []

    with client.websocket_connect(f"/ws/{sid}") as ws:
        state = ws.receive_json()
        assert state["type"] == "session_state"

        for line in lines:
            ws.send_json({"type": "chat", "text": line})
            turn_end = _drain_until(ws, events, lambda e: e["type"] == "assistant_message")
            assert turn_end is not None, "never received assistant_message for a turn"

        solved = _drain_until(
            ws,
            events,
            lambda e: e["type"] == "solve_completed" and e["schedule"]["status"] == "solved",
        )
        assert solved is not None, f"no solved schedule; saw types={[e['type'] for e in events]}"

    types = [e["type"] for e in events]
    assert "user_message" in types
    assert "assistant_delta" in types  # tokens streamed
    assert "spec_updated" in types
    assert "solve_started" in types

    payload = solved["schedule"]
    assert payload["tournament_name"] == "Fall Classic"
    assert len(payload["fields"]) == 2
    assert len(payload["teams"]) == 8
    assert payload["stats"]["num_games_scheduled"] > 0
    # Every game carries display names + a grid offset for the timeline.
    a_field = next(f for f in payload["fields"] if f["games"])
    g = a_field["games"][0]
    assert g["home"] and g["away"] and g["field_name"]
    assert "start_offset_min" in g and "duration_min" in g


def test_ws_persists_rules_across_reads() -> None:
    client = _client()
    sid = client.post("/api/sessions").json()["id"]

    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.receive_json()  # session_state
        ws.send_json({"type": "chat", "text": "kick things off"})
        # spec_updated now also fires mid-turn (per-mutation push); the turn —
        # and thus store persistence — completes at assistant_message.
        _drain_until(ws, [], lambda e: e["type"] == "assistant_message")
        _drain_until(ws, [], lambda e: e["type"] == "spec_updated")

    # After the first turn the tournament name + a division are captured and
    # visible through the read-only REST view (served from the store).
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["rules"]["tournament"]["name"] == "Fall Classic"
    assert len(detail["rules"]["divisions"]) >= 1
    assert len(detail["transcript"]) == 2  # director + agent
