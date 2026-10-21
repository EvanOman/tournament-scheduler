"""Admission stays bounded across workers, restart, concurrent writes and failure."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

import pytest
from fastapi.testclient import TestClient

from demo.api.admission import LOCK_KEY, STATE_KEY, ChatAdmission, Unavailable, create_admission_app


class PersistentStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.lock = Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return self.data.get(key, default)

    def put(self, key: str, value: Any, *, skip_if_exists: bool = False) -> bool:
        with self.lock:
            if skip_if_exists and key in self.data:
                return False
            self.data[key] = value
            return True

    def pop(self, key: str) -> Any:
        with self.lock:
            return self.data.pop(key)


def test_daily_client_cap_survives_new_authority_and_releases() -> None:
    store = PersistentStore()
    for index in range(15):
        guard = ChatAdmission(store)  # a fresh container each time
        assert guard.change("client", str(index), now=1000) == 200
        assert guard.change(None, str(index), now=1001) == 200
    assert ChatAdmission(store).change("client", "sixteen", now=1002) == 429
    assert ChatAdmission(store).change("client", "tomorrow", now=1000 + 86400) == 200


def test_global_cap_is_shared_across_clients_and_origins() -> None:
    store = PersistentStore()
    for index in range(400):
        guard = ChatAdmission(store)
        assert guard.change(str(index), str(index), now=1000) == 200
        guard.change(None, str(index), now=1001)
    assert ChatAdmission(store).change("new-client", "new-request", now=1002) == 429
    assert store.data[STATE_KEY]["total"] == 400


def test_inflight_limits_expire_and_duplicates_do_not_charge_twice() -> None:
    store = PersistentStore()
    guard = ChatAdmission(store)
    assert guard.change("a", "one", now=1000) == 200
    assert guard.change("a", "one", now=1001) == 200
    assert store.data[STATE_KEY]["total"] == 1
    assert guard.change("a", "two", now=1001) == 429
    for index in range(3):
        assert guard.change(str(index), str(index), now=1001) == 200
    assert guard.change("fifth", "five", now=1001) == 429
    assert guard.change("a", "after-expiry", now=1122) == 200
    assert store.data[STATE_KEY]["total"] == 5


def test_overlapping_writers_never_over_admit() -> None:
    store = PersistentStore()

    def reserve(index: int) -> int:
        try:
            return ChatAdmission(store).change(str(index), str(index), now=1000)
        except Unavailable:
            return 503

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(reserve, range(100)))
    assert results.count(200) <= 4
    assert store.data[STATE_KEY]["total"] == results.count(200)
    assert set(results) <= {200, 429, 503}


def test_crashed_writer_and_failed_durable_write_fail_closed(monkeypatch: Any) -> None:
    store = PersistentStore()
    store.data[LOCK_KEY] = True
    with pytest.raises(Unavailable):
        ChatAdmission(store).change("a", "one", now=1000)
    assert STATE_KEY not in store.data
    store.data.pop(LOCK_KEY)
    original_put = store.put

    def fail_state(key: str, value: Any, *, skip_if_exists: bool = False) -> bool:
        if key == STATE_KEY:
            raise OSError("store unavailable")
        return original_put(key, value, skip_if_exists=skip_if_exists)

    monkeypatch.setattr(store, "put", fail_state)
    with pytest.raises(Unavailable):
        ChatAdmission(store).change("a", "one", now=1000)
    assert STATE_KEY not in store.data


def test_admission_http_auth_and_validation_precede_storage(monkeypatch: Any) -> None:
    monkeypatch.setenv("TOURNEYDESK_ORIGIN_SECRET", "synthetic-origin-key-for-test-only")
    store = PersistentStore()
    client = TestClient(create_admission_app(store))
    assert client.post("/reserve", content=b"not JSON").status_code == 403
    assert not store.data
    headers = {"X-Demo-Secret": "synthetic-origin-key-for-test-only"}
    assert client.post("/reserve", content=b"{}", headers=headers).status_code == 422
    assert client.post("/reserve", content=b"x" * 1025, headers=headers).status_code == 413
    assert not store.data
    request = {"client_id": "a" * 64, "request_id": "b" * 32}
    response = client.post("/reserve", json=request, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"lease_id": "b" * 32}
    assert client.post("/release", json={"lease_id": "b" * 32}, headers=headers).status_code == 200
    assert store.data[STATE_KEY]["total"] == 1
