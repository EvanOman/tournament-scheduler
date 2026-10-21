"""Explicit offline boundary for tests exercising an authorized demo chat."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def demo_chat_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from demo.api import security
    from demo.api.main import app

    async def reserve(_client_id: str, _request_id: str) -> int:
        return 200

    async def release(_request_id: str) -> None:
        return None

    secret = "synthetic-origin-key-for-test-only"
    monkeypatch.setenv("TOURNEYDESK_ORIGIN_SECRET", secret)
    monkeypatch.setattr(security, "reserve_chat", reserve)
    monkeypatch.setattr(security, "release_chat", release)
    return TestClient(app, headers={"X-Demo-Secret": secret, "X-Demo-Client-Id": "a" * 64})
