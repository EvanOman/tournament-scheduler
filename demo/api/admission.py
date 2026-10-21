"""Persistent chat admission shared by every public demo origin.

The Dict's atomic insert protects the whole read/modify/write, including
overlapping deployments. A crashed lock fails closed; an operator must verify
that no writer is running before clearing it. Never guess that a lock is stale.
"""

from __future__ import annotations

import copy
import json
import time
from datetime import UTC, datetime
from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from demo.api.security import origin_status

DAILY_CLIENT_LIMIT = 15
DAILY_GLOBAL_LIMIT = 400
MAX_IN_FLIGHT = 4
LEASE_SECONDS = 120
STATE_KEY = "state-v1"
LOCK_KEY = "writer-v1"


class Store(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def put(self, key: str, value: Any, *, skip_if_exists: bool = False) -> bool: ...
    def pop(self, key: str) -> Any: ...


class Unavailable(Exception):
    """No admission may be granted without a confirmed durable write."""


class Lease(TypedDict):
    client: str
    expires: float


class State(TypedDict):
    day: str
    total: int
    clients: dict[str, int]
    leases: dict[str, Lease]


class ChatAdmission:
    def __init__(self, store: Store) -> None:
        self.store = store

    def change(self, client_id: str | None, request_id: str, *, now: float | None = None) -> int:
        """Reserve once or release an in-flight lease; daily claims are never refunded."""
        now = time.time() if now is None else now
        try:
            locked = self.store.put(LOCK_KEY, True, skip_if_exists=True)
        except Exception as exc:
            raise Unavailable from exc
        if not locked:
            raise Unavailable
        try:
            day = datetime.fromtimestamp(now, UTC).date().isoformat()
            saved = self.store.get(STATE_KEY)
            state: State = (
                copy.deepcopy(saved) if saved is not None else {"day": day, "total": 0, "clients": {}, "leases": {}}
            )
            if state["day"] != day:
                state.update(day=day, total=0, clients={})
            leases = state["leases"] = {key: value for key, value in state["leases"].items() if value["expires"] > now}
            status = 200
            if client_id is None:
                leases.pop(request_id, None)
            elif request_id in leases:
                if leases[request_id]["client"] != client_id:
                    raise Unavailable
            elif (
                state["total"] >= DAILY_GLOBAL_LIMIT
                or state["clients"].get(client_id, 0) >= DAILY_CLIENT_LIMIT
                or len(leases) >= MAX_IN_FLIGHT
                or any(lease["client"] == client_id for lease in leases.values())
            ):
                status = 429
            else:
                state["total"] += 1
                state["clients"][client_id] = state["clients"].get(client_id, 0) + 1
                leases[request_id] = {"client": client_id, "expires": now + LEASE_SECONDS}
            self.store.put(STATE_KEY, state)
            return status
        except Exception as exc:
            raise Unavailable from exc
        finally:
            try:
                self.store.pop(LOCK_KEY)
            except Exception as exc:
                raise Unavailable from exc


class Reserve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: str = Field(pattern=r"^[0-9a-f]{32}$")


def create_admission_app(store: Store) -> Starlette:
    admission = ChatAdmission(store)

    async def change(request: Request) -> JSONResponse:
        status = origin_status(dict(request.headers))
        if status is not None:
            return JSONResponse({"detail": "Admission unavailable"}, status_code=status)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
        try:
            if request.url.path == "/reserve":
                reserve = Reserve.model_validate_json(bytes(body))
                client_id, request_id = reserve.client_id, reserve.request_id
            else:
                release = Release.model_validate_json(bytes(body))
                client_id, request_id = None, release.lease_id
        except (ValidationError, json.JSONDecodeError):
            return JSONResponse({"detail": "Invalid request"}, status_code=422)
        try:
            status = admission.change(client_id, request_id)
        except Unavailable:
            return JSONResponse({"detail": "Admission unavailable"}, status_code=503)
        payload = {"lease_id": request_id} if status == 200 else {"detail": "Chat allowance reached"}
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    return Starlette(routes=[Route("/reserve", change, methods=["POST"]), Route("/release", change, methods=["POST"])])
