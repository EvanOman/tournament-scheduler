"""Fail-closed origin authentication and shared admission before paid chat."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ORIGIN_SECRET_ENV = "TOURNEYDESK_ORIGIN_SECRET"
CLIENT_HEADER = "x-demo-client-id"
MAX_CHAT_BYTES = 8192
_CLIENT_ID = re.compile(r"^[0-9a-f]{64}$")


def origin_status(headers: dict[str, str]) -> int | None:
    expected = os.environ.get(ORIGIN_SECRET_ENV, "")
    if len(expected) < 32:
        return 503
    supplied = headers.get("x-demo-secret", "")
    if len(supplied) > 256 or not hmac.compare_digest(supplied.encode(), expected.encode()):
        return 403
    return None


def admission_url() -> str:
    url = os.environ.get("TOURNEYDESK_ADMISSION_URL", "").rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("Admission service is not configured")
    return url


async def reserve_chat(client_id: str, request_id: str) -> int:
    """Return an HTTP status; neither an unavailable guard nor a retry admits work."""
    import httpx  # noqa: PLC0415 -- health/solver startup must stay small

    try:
        url = admission_url()
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(
                f"{url}/reserve",
                headers={"X-Demo-Secret": os.environ.get(ORIGIN_SECRET_ENV, "")},
                json={"client_id": client_id, "request_id": request_id},
            )
        if response.status_code == 429:
            return 429
        if response.status_code != 200 or response.json() != {"lease_id": request_id}:
            return 503
    except (httpx.HTTPError, ValueError):
        return 503
    return 200


async def release_chat(request_id: str) -> None:
    import httpx  # noqa: PLC0415

    # Expiring leases recover from cancellation, network errors and killed workers.
    # Daily claims are never refunded, including failed provider requests.
    with contextlib.suppress(httpx.HTTPError, ValueError):
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            await client.post(
                f"{admission_url()}/release",
                headers={"X-Demo-Secret": os.environ.get(ORIGIN_SECRET_ENV, "")},
                json={"lease_id": request_id},
            )


class ChatSecurityMiddleware:
    """Authenticate before reading a bounded body; hold admission through SSE completion."""

    def __init__(self, app: ASGIApp, validators: Mapping[str, Callable[[bytes], Any]]) -> None:
        self.app = app
        self.validators = validators

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "").rstrip("/")
        if scope["type"] != "http" or scope.get("method") != "POST" or path not in self.validators:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        status = origin_status(headers)
        client_id = headers.get(CLIENT_HEADER, "")
        if status is None and not _CLIENT_ID.fullmatch(client_id):
            status = 403
        if status is not None:
            await self._reject(scope, receive, send, status)
            return
        try:
            declared = int(headers.get("content-length", "0"))
        except ValueError:
            declared = -1
        if declared < 0 or declared > MAX_CHAT_BYTES:
            await self._reject(scope, receive, send, 413)
            return
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body.extend(event.get("body", b""))
            if len(body) > MAX_CHAT_BYTES:
                await self._reject(scope, receive, send, 413)
                return
            if not event.get("more_body", False):
                break
        # Validate before charging quota. Authorized malformed live probes cannot
        # construct a provider, reserve allowance or invoke a model.
        try:
            self.validators[path](bytes(body))
        except ValueError:
            await self._reject(scope, receive, send, 422)
            return
        request_id = uuid.uuid4().hex
        status = await reserve_chat(client_id, request_id)
        if status != 200:
            await self._reject(scope, receive, send, status)
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        try:
            await self.app(scope, replay, send)
        finally:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(release_chat(request_id))

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int) -> None:
        messages = {
            403: "Please use the chat on the website.",
            413: "Chat request is too large.",
            422: "Invalid chat request.",
            429: "Chat allowance reached. Please try again later.",
            503: "Chat is temporarily unavailable.",
        }
        message = messages[status]
        response = JSONResponse({"detail": message}, status_code=status, headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)
