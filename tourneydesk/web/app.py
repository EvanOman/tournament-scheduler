"""FastAPI app: serves the built SPA, REST for sessions/spec/schedule, and a
per-session WebSocket for streaming chat + server-pushed live updates.

The web app owns no conversation or solve logic -- it drives the shared
`IntakeService` (via `SessionManager`) and `SpeculativeSolver` from `core`, so
the terminal and the browser exercise the identical code path.

WebSocket protocol (JSON both directions), keyed to one session id:

  client -> server
    {"type": "chat", "text": "..."}          director sends a message

  server -> client
    {"type": "session_state", "rules": {...}, "transcript": [...]}   on connect
    {"type": "user_message", "text": "..."}                          echo of director msg
    {"type": "assistant_delta", "text": "..."}                       streaming token chunk
    {"type": "assistant_message", "text": "...",
        "echoes": [...], "complete": bool}                           final turn
    {"type": "spec_updated", "rules": {...}}                         full Rules state
    {"type": "solve_started"}                                        speculative solve began
    {"type": "solve_completed", "schedule": {...}}                   solve payload (any status)
    {"type": "conflict_detected", "detail": {...}}                   extra signal on infeasibility
    {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from tourneydesk.core.speculative import SpeculativeSolver
from tourneydesk.providers.base import IntakeProvider, TextDelta
from tourneydesk.providers.claude import ClaudeIntake
from tourneydesk.providers.fake import FakeIntake
from tourneydesk.session import IncompleteSpecError, SpecSession
from tourneydesk.web.canned import CANNED_SCRIPT
from tourneydesk.web.manager import LiveSession, ProviderFactory, SessionManager
from tourneydesk.web.schedule_view import schedule_payload
from tourneydesk.web.store import SessionStore

STATIC_DIR = Path(__file__).parent / "static"


def claude_factory(session: SpecSession) -> IntakeProvider:
    return ClaudeIntake(session)


def fake_factory(session: SpecSession) -> IntakeProvider:
    return FakeIntake(session, list(CANNED_SCRIPT))


def create_app(
    *,
    db_path: str = ":memory:",
    provider_factory: ProviderFactory | None = None,
    debounce_seconds: float = 1.5,
    static_dir: Path | None = None,
) -> FastAPI:
    store = SessionStore(db_path)
    manager = SessionManager(store, provider_factory or claude_factory)
    app = FastAPI(title="TourneyDesk")
    app.state.manager = manager
    app.state.debounce_seconds = debounce_seconds

    # -- REST ---------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        return [row.summary() for row in manager.list_rows()]

    @app.post("/api/sessions")
    async def create_session(body: dict[str, Any] | None = None) -> dict[str, Any]:
        title = (body or {}).get("title") or "Untitled tournament"
        row = manager.create(title)
        return row.summary()

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str) -> JSONResponse:
        row = manager.get_row(sid)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({**row.summary(), "rules": row.rules, "transcript": row.transcript})

    @app.get("/api/sessions/{sid}/spec")
    def get_spec(sid: str) -> JSONResponse:
        live = manager.live(sid)
        if live is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            spec, assumptions = live.service.to_spec()
        except IncompleteSpecError as exc:
            return JSONResponse({"complete": False, "missing": exc.missing})
        return JSONResponse({"complete": True, "assumptions": assumptions, "spec": spec.model_dump(mode="json")})

    @app.get("/api/sessions/{sid}/schedule")
    def get_schedule(sid: str) -> JSONResponse:
        live = manager.live(sid)
        if live is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        outcome = live.service.try_solve()
        live.last_outcome = outcome
        return JSONResponse(schedule_payload(outcome))

    # -- WebSocket ----------------------------------------------------------

    @app.websocket("/ws/{sid}")
    async def ws_session(ws: WebSocket, sid: str) -> None:
        await ws.accept()
        live = manager.live(sid)
        if live is None:
            await ws.send_json({"type": "error", "message": f"Unknown session '{sid}'."})
            await ws.close()
            return

        await ws.send_json(
            {"type": "session_state", "rules": live.session.to_rules_json(), "transcript": live.transcript}
        )

        solver = _build_solver(ws, live, app.state.debounce_seconds)
        # Surface any schedule a rejoined/complete session already implies.
        solver.trigger()

        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") != "chat":
                    continue
                text = str(msg.get("text", "")).strip()
                if not text:
                    continue
                try:
                    await _handle_chat(ws, live, manager, solver, text)
                except WebSocketDisconnect:
                    raise
                except Exception as exc:
                    # A failed turn must not kill the connection loop — the director
                    # would see their message silently vanish with no way to recover.
                    logging.getLogger("tourneydesk").exception("chat turn failed")
                    await ws.send_json({"type": "error", "message": _user_error_message(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            await solver.aclose()

    # -- Static SPA (registered last so /api and /ws win) -------------------

    resolved_static = static_dir or STATIC_DIR
    if resolved_static.exists():
        app.mount("/", StaticFiles(directory=str(resolved_static), html=True), name="spa")
    else:

        @app.get("/")
        def _placeholder() -> JSONResponse:
            return JSONResponse(
                {"message": "TourneyDesk API is running. Frontend assets not built yet.", "api": "/api/health"}
            )

    return app


def _user_error_message(exc: Exception) -> str:
    """Turn a failed-turn exception into an honest, actionable user message.

    A retryable-sounding generic message on a permanent outage sends users into
    a retry loop with no signal (persona P4 retried an out-of-credits backend
    four times). Distinguish the known-permanent cases.
    """
    text = str(exc)
    if "credit balance" in text.lower():
        return (
            "The scheduling assistant can't reach its AI service right now (the account "
            "is out of credits). Retrying won't help until the operator tops it up — "
            "your draft is safe and will be here when service resumes."
        )
    if "authentication" in text.lower() or "api key" in text.lower():
        return (
            "The scheduling assistant can't authenticate with its AI service. This needs "
            "the operator's attention — your draft is safe."
        )
    return "Something went wrong handling that message. Your draft is safe — please send it again."


def _build_solver(ws: WebSocket, live: LiveSession, debounce: float) -> SpeculativeSolver:
    async def on_started() -> None:
        await ws.send_json({"type": "solve_started"})

    async def on_result(outcome: Any) -> None:
        live.last_outcome = outcome
        payload = schedule_payload(outcome)
        await ws.send_json({"type": "solve_completed", "schedule": payload})
        if outcome.status in ("infeasible", "invalid"):
            await ws.send_json({"type": "conflict_detected", "detail": payload})

    return SpeculativeSolver(live.service.try_solve, on_started, on_result, debounce_seconds=debounce)


async def _handle_chat(
    ws: WebSocket, live: LiveSession, manager: SessionManager, solver: SpeculativeSolver, text: str
) -> None:
    await ws.send_json({"type": "user_message", "text": text})

    loop = asyncio.get_running_loop()
    event_q: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

    def on_delta(chunk: str) -> None:
        # Called from the worker thread; hop back onto the event loop safely.
        loop.call_soon_threadsafe(event_q.put_nowait, ("text", chunk))

    def on_spec_mutated() -> None:
        # Push Rules-panel state and re-arm the speculative solve after EVERY
        # mutation, not just at turn end — long multi-tool turns left the panels
        # stale for 90s+ while the streamed text claimed changes had landed.
        loop.call_soon_threadsafe(event_q.put_nowait, ("spec", ""))

    async def pump() -> None:
        while True:
            item = await event_q.get()
            if item is None:
                return
            kind, chunk = item
            if kind == "text":
                await ws.send_json({"type": "assistant_delta", "text": chunk})
            else:
                await ws.send_json({"type": "spec_updated", "rules": live.session.to_rules_json()})
                solver.trigger()

    pump_task = asyncio.create_task(pump())
    try:
        turn = await asyncio.to_thread(_run_send, live.service, text, on_delta, on_spec_mutated)
    finally:
        event_q.put_nowait(None)
        await pump_task

    await ws.send_json(
        {"type": "assistant_message", "text": turn.text, "echoes": turn.echoes, "complete": turn.complete}
    )

    manager.record_turn(live, text, turn.text, turn.echoes)
    await ws.send_json({"type": "spec_updated", "rules": live.session.to_rules_json()})

    if turn.tool_calls:
        solver.trigger()


def _run_send(service: Any, text: str, on_delta: TextDelta, on_spec_mutated: Any = None) -> Any:
    """Run one async provider turn to completion in a worker thread.

    A fresh event loop per call keeps the (possibly blocking) Anthropic SDK off
    the main loop; the FakeIntake pacing sleeps also run here.
    """
    return asyncio.run(service.send(text, on_delta, on_spec_mutated))
