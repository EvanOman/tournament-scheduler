"""Public origin checks must finish before chat creates or runs a provider."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from demo.api import security

SECRET = "synthetic-origin-key-for-test-only"
HEADERS = {"X-Demo-Secret": SECRET, "X-Demo-Client-Id": "a" * 64}


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_direct_chat_rejected_before_provider_construction(monkeypatch: Any, path: str) -> None:
    from demo.api.main import _CHAT_STORE, app

    monkeypatch.setenv("TOURNEYDESK_ORIGIN_SECRET", SECRET)
    created = []

    def create(*args: Any) -> None:
        created.append(args)
        raise AssertionError("unauthorized request reached a provider")

    monkeypatch.setattr(_CHAT_STORE, "get_or_create", create)
    response = TestClient(app, raise_server_exceptions=False).post(path, json={"message": "hello"})

    assert response.status_code == 403
    assert created == []


def test_missing_origin_configuration_fails_closed(monkeypatch: Any) -> None:
    from demo.api.main import _CHAT_STORE, app

    monkeypatch.delenv("TOURNEYDESK_ORIGIN_SECRET", raising=False)

    def forbidden(*_args: Any) -> None:
        raise AssertionError("provider")

    monkeypatch.setattr(_CHAT_STORE, "get_or_create", forbidden)
    response = TestClient(app, raise_server_exceptions=False).post("/chat", json={"message": "hello"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Chat is temporarily unavailable."}


@pytest.mark.parametrize("headers", [{}, {**HEADERS, "X-Demo-Secret": "wrong"}, {"X-Demo-Secret": SECRET}])
def test_authentication_precedes_body_read_and_quota(monkeypatch: Any, headers: dict[str, str]) -> None:
    monkeypatch.setenv("TOURNEYDESK_ORIGIN_SECRET", SECRET)

    async def forbidden(*_args: Any) -> Any:
        raise AssertionError("unauthorized request read a body, reserved quota or reached the app")

    middleware = security.ChatSecurityMiddleware(forbidden, {"/chat": json.loads})
    monkeypatch.setattr(security, "reserve_chat", forbidden)
    sent = []

    async def send(event: Any) -> None:
        sent.append(event)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/chat",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    }
    asyncio.run(middleware(scope, forbidden, send))
    assert sent[0]["status"] == 403


@pytest.mark.parametrize("content", [b"{}", b"not JSON", b"x" * 8193])
def test_invalid_or_oversized_chat_never_reserves_or_builds_a_provider(monkeypatch: Any, content: bytes) -> None:
    from demo.api.main import _CHAT_STORE, app

    monkeypatch.setenv("TOURNEYDESK_ORIGIN_SECRET", SECRET)

    def forbidden(*_args: Any) -> Any:
        raise AssertionError("invalid request reached paid work")

    monkeypatch.setattr(security, "reserve_chat", forbidden)
    monkeypatch.setattr(_CHAT_STORE, "get_or_create", forbidden)
    response = TestClient(app).post("/chat", content=content, headers=HEADERS)
    assert response.status_code == (413 if len(content) > 8192 else 422)


@pytest.mark.parametrize("status", [429, 503])
def test_shared_admission_rejection_precedes_provider(monkeypatch: Any, status: int) -> None:
    from demo.api.main import _CHAT_STORE, app

    monkeypatch.setenv("TOURNEYDESK_ORIGIN_SECRET", SECRET)

    async def rejected(*_args: Any) -> int:
        return status

    def forbidden(*_args: Any) -> Any:
        raise AssertionError("rejected request constructed provider")

    monkeypatch.setattr(security, "reserve_chat", rejected)
    monkeypatch.setattr(_CHAT_STORE, "get_or_create", forbidden)
    response = TestClient(app).post("/chat", json={"message": "hello"}, headers=HEADERS)
    assert response.status_code == status


def test_chunked_body_cannot_bypass_byte_limit(monkeypatch: Any) -> None:
    monkeypatch.setenv("TOURNEYDESK_ORIGIN_SECRET", SECRET)

    async def forbidden(*_args: Any) -> Any:
        raise AssertionError("oversized request reached paid work")

    middleware = security.ChatSecurityMiddleware(forbidden, {"/chat": json.loads})
    monkeypatch.setattr(security, "reserve_chat", forbidden)
    sent = []
    chunks = iter([b"a" * 4096, b"b" * 4097])

    async def receive() -> Any:
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    async def send(event: Any) -> None:
        sent.append(event)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/chat",
        "headers": [(key.lower().encode(), value.encode()) for key, value in HEADERS.items()],
    }
    asyncio.run(middleware(scope, receive, send))
    assert sent[0]["status"] == 413


@pytest.mark.parametrize("app_fails", [False, True])
def test_lease_covers_entire_stream_and_is_released_on_error(monkeypatch: Any, app_fails: bool) -> None:
    monkeypatch.setenv("TOURNEYDESK_ORIGIN_SECRET", SECRET)
    events = []

    async def reserve(_client: str, request: str) -> int:
        events.append(("reserve", request))
        return 200

    async def release(request: str) -> None:
        events.append(("release", request))

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        assert (await receive())["body"] == b"{}"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"delta", "more_body": True})
        assert [event[0] for event in events] == ["reserve"]
        if app_fails:
            raise RuntimeError("stream stopped")
        await send({"type": "http.response.body", "body": b"final", "more_body": False})

    monkeypatch.setattr(security, "reserve_chat", reserve)
    monkeypatch.setattr(security, "release_chat", release)
    middleware = security.ChatSecurityMiddleware(inner, {"/chat/stream": json.loads})
    client = TestClient(middleware, headers=HEADERS, raise_server_exceptions=False)
    client.post("/chat/stream", content=b"{}")
    assert [event[0] for event in events] == ["reserve", "release"]
    assert events[0][1] == events[1][1]


def test_unavailable_or_bad_admission_configuration_fails_closed(monkeypatch: Any) -> None:
    monkeypatch.delenv("TOURNEYDESK_ADMISSION_URL", raising=False)
    assert asyncio.run(security.reserve_chat("a" * 64, "b" * 32)) == 503
    monkeypatch.setenv("TOURNEYDESK_ADMISSION_URL", "http://localhost/unsafe")
    assert asyncio.run(security.reserve_chat("a" * 64, "b" * 32)) == 503


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_model_deadline_cancels_work_before_lease_release(
    monkeypatch: Any, demo_chat_client: TestClient, path: str
) -> None:
    from types import SimpleNamespace

    from demo.api import main

    events = []

    async def stalled(*_args: Any, **_kwargs: Any) -> None:
        try:
            await asyncio.sleep(10)
        finally:
            events.append("cancelled")

    async def release(_request: str) -> None:
        events.append("released")

    monkeypatch.setattr(main, "_CHAT_MODEL_TIMEOUT_S", 0.01)
    monkeypatch.setattr(main._CHAT_STORE, "get_or_create", lambda *_args: ("test", SimpleNamespace(send=stalled)))
    monkeypatch.setattr(security, "release_chat", release)
    response = demo_chat_client.post(path, json={"message": "hello"})
    if path == "/chat":
        assert response.status_code == 504
    else:
        assert response.status_code == 200
        assert "event: error" in response.text
        assert "event: final" not in response.text
    assert events == ["cancelled", "released"]


def test_sessions_are_bounded_and_bound_to_trusted_client_identity() -> None:
    from demo.api.main import _ChatSessionStore
    from tourneydesk.limits import PUBLIC_DEMO_LIMITS

    store = _ChatSessionStore(max_sessions=1, idle_ttl_s=3600)
    first_id, first = store.get_or_create(None, "a" * 64)
    assert first.session.limits is PUBLIC_DEMO_LIMITS
    assert store.get_or_create(first_id, "a" * 64) == (first_id, first)
    second_id, second = store.get_or_create(first_id, "b" * 64)
    assert second_id != first_id
    assert second is not first
    assert len(store._items) == len(store._owners) == 1
    assert first_id not in store._owners


def test_public_solve_rejects_expensive_shape_before_executor_submission(monkeypatch: Any) -> None:
    from demo.api import main

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("oversized model reached executor")

    monkeypatch.setattr(main._EXECUTOR, "submit", forbidden)
    request = {
        "days": 2,
        "buffer_minutes": 0,
        "divisions": [
            {"name": str(index), "teams": count, "game_minutes": 15, "games_per_team": 5, "min_rest_minutes": 180}
            for index, count in enumerate([24, 24, 16])
        ],
        "fields": [{"name": str(index), "open": "00:00", "close": "23:59"} for index in range(8)],
    }
    response = TestClient(main.app).post("/solve", json=request)
    assert response.status_code == 422
    assert response.json() == {"detail": "Demo workload too large; reduce teams, fields or availability."}
