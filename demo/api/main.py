"""TourneyDesk demo API for evanoman.com.

A purpose-built, LLM-free FastAPI service: visitors build tournament
constraints with direct UI controls (the conversational intake is the full
product and is deliberately absent here), and this API runs the real CP-SAT
solver, independent validator, and — on infeasibility — the deterministic
conflict-explanation + repair engine.

No ANTHROPIC_API_KEY, no secrets: everything here is pure computation.
Deployed on Render free tier; the site proxies /api/tourneydesk/* to it
(mirrors the linprogx demo pattern).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tournament_scheduler.conflict import extract_conflict
from tournament_scheduler.models import (
    CoachingConflict,
    DivisionSpec,
    FieldSize,
    FieldSpec,
    TeamSpec,
    TimeWindow,
    TournamentSpec,
)
from tournament_scheduler.pools import assign_pools
from tournament_scheduler.solver import solve
from tournament_scheduler.validator import validate
from tourneydesk.core.service import SolveOutcome, solve_current
from tourneydesk.session import SpecSession
from tourneydesk.web.schedule_view import schedule_payload

if TYPE_CHECKING:
    from tourneydesk.providers.pydantic_ai import PydanticAIIntake

logger = logging.getLogger(__name__)

app = FastAPI(title="TourneyDesk Demo API", version="0.2.0", docs_url=None, redoc_url=None)

ALLOWED_ORIGINS = [
    "https://evanoman.com",
    "https://www.evanoman.com",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:8788",  # wrangler pages dev
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
)

# One solve at a time is plenty for a personal-site demo, and it bounds the
# free-tier instance's memory/CPU exposure regardless of request volume.
_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_SOLVE_SECONDS = 8
_REQUEST_TIMEOUT_S = 25

# The demo runs on a fixed imaginary weekend; only day count is configurable.
_SATURDAY = "2027-06-12"
_SUNDAY = "2027-06-13"


class DemoDivision(BaseModel):
    name: str = Field(min_length=1, max_length=24)
    teams: int = Field(ge=4, le=24)
    game_minutes: int = Field(ge=15, le=90)
    games_per_team: int = Field(ge=1, le=5)
    min_rest_minutes: int = Field(ge=0, le=180)


class DemoField(BaseModel):
    name: str = Field(min_length=1, max_length=24)
    open: str = Field(pattern=r"^\d{2}:\d{2}$")  # "08:00"
    close: str = Field(pattern=r"^\d{2}:\d{2}$")


class DemoCoachConflict(BaseModel):
    coach_name: str = Field(min_length=1, max_length=24)
    division: int = Field(ge=0)  # index into divisions
    team_numbers: list[int] = Field(min_length=2, max_length=4)  # 1-based within the division


class DemoRequest(BaseModel):
    days: Literal[1, 2] = 1
    buffer_minutes: int = Field(default=10, ge=0, le=30)
    divisions: list[DemoDivision] = Field(min_length=1, max_length=4)
    fields: list[DemoField] = Field(min_length=1, max_length=8)
    coach_conflicts: list[DemoCoachConflict] = Field(default_factory=list, max_length=4)


def _build_spec(req: DemoRequest) -> TournamentSpec:
    days = [_SATURDAY, _SUNDAY][: req.days]
    fields = []
    for i, f in enumerate(req.fields):
        windows = []
        for day in days:
            start = datetime.fromisoformat(f"{day}T{f.open}")
            end = datetime.fromisoformat(f"{day}T{f.close}")
            if end <= start:
                raise HTTPException(status_code=422, detail=f"Field '{f.name}': close must be after open.")
            windows.append(TimeWindow(start=start, end=end))
        fields.append(FieldSpec(id=f"f{i + 1}", name=f.name, size=FieldSize.FULL, availability=windows))

    divisions, teams = [], []
    for di, d in enumerate(req.divisions):
        div_id = f"d{di + 1}"
        divisions.append(
            DivisionSpec(
                id=div_id,
                name=d.name,
                field_size=FieldSize.FULL,
                game_duration_minutes=d.game_minutes,
                buffer_minutes=req.buffer_minutes,
                min_rest_minutes=d.min_rest_minutes,
                games_per_team=d.games_per_team,
                bracket_after_pools=False,
            )
        )
        teams.extend(
            TeamSpec(id=f"{div_id}_t{n}", name=f"{d.name} Team {n}", division_id=div_id) for n in range(1, d.teams + 1)
        )

    conflicts = []
    for c in req.coach_conflicts:
        if c.division >= len(req.divisions):
            raise HTTPException(status_code=422, detail=f"Coach '{c.coach_name}': division index out of range.")
        div_id = f"d{c.division + 1}"
        max_team = req.divisions[c.division].teams
        team_ids = []
        for n in c.team_numbers:
            if not 1 <= n <= max_team:
                raise HTTPException(status_code=422, detail=f"Coach '{c.coach_name}': team {n} out of range.")
            team_ids.append(f"{div_id}_t{n}")
        conflicts.append(CoachingConflict(coach_name=c.coach_name, team_ids=team_ids))

    return TournamentSpec(
        name="TourneyDesk demo tournament",
        divisions=divisions,
        teams=teams,
        fields=fields,
        coaching_conflicts=conflicts,
        max_solve_seconds=_SOLVE_SECONDS,
    )


def _solve_and_shape(spec: TournamentSpec) -> dict[str, Any]:
    pools = assign_pools(spec)
    schedule = solve(spec, pools)
    status = schedule.stats.status

    if status in ("OPTIMAL", "FEASIBLE"):
        result = validate(schedule, spec)
        outcome = SolveOutcome(
            status="solved" if result.valid else "invalid",
            schedule=schedule,
            validation=result,
            spec=spec,
        )
        return {"result": "solved", "schedule": schedule_payload(outcome)}

    if status == "INFEASIBLE":
        conflict = extract_conflict(spec, pools, time_limit_s=_SOLVE_SECONDS)
        payload: dict[str, Any] = {"result": "infeasible", "explanation": None}
        if conflict is not None:
            # The deterministic path does not need an LLM provider. Import the
            # explanation stack only for the uncommon infeasible solve.
            from tourneydesk.explain.engine import explain_conflict

            explanation = explain_conflict(spec, conflict, use_llm=False)
            payload["explanation"] = explanation.model_dump(mode="json")
        return payload

    return {"result": "inconclusive"}


# ---------------------------------------------------------------------------
# Conversational chat (Pydantic AI, GLM/GPT), additive to the LLM-free /solve.
# ---------------------------------------------------------------------------

# Render's free tier is a single instance that restarts on idle/deploy, so chat
# state is deliberately in-memory and disposable: a bounded LRU keyed by a
# server-minted session id. Losing it on restart is acceptable for a demo (the
# client just gets a fresh session_id on its next call). The bound keeps a busy
# day or an abuse spike from growing memory without limit. One intake per session
# owns BOTH engines and a shared history, so switching models mid-chat continues
# the same conversation.
_CHAT_MAX_SESSIONS = 500
_CHAT_IDLE_TTL_S = 3600  # evict a session after 1h with no activity


class _ChatSessionStore:
    """Bounded, idle-evicting map of session_id -> (PydanticAIIntake, last_seen)."""

    def __init__(self, max_sessions: int, idle_ttl_s: float) -> None:
        self._max = max_sessions
        self._ttl = idle_ttl_s
        self._items: OrderedDict[str, tuple[PydanticAIIntake, float]] = OrderedDict()

    def _evict(self, now: float) -> None:
        # Drop anything idle past the TTL, then trim to capacity (oldest first).
        expired = [sid for sid, (_, seen) in self._items.items() if now - seen > self._ttl]
        for sid in expired:
            del self._items[sid]
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def get_or_create(self, session_id: str | None) -> tuple[str, PydanticAIIntake]:
        now = time.monotonic()
        self._evict(now)
        if session_id and session_id in self._items:
            intake, _ = self._items[session_id]
            self._items.move_to_end(session_id)
            self._items[session_id] = (intake, now)
            return session_id, intake
        # Unknown or absent id -> mint a fresh session. (An expired/unknown id is
        # treated as new rather than errored so the client never gets stuck.)
        new_id = uuid.uuid4().hex
        # Keep Pydantic AI and its provider adapters off the health/solver cold
        # path. The first chat session is the first request that needs them.
        from tourneydesk.providers.pydantic_ai import PydanticAIIntake

        intake = PydanticAIIntake(SpecSession())
        self._items[new_id] = (intake, now)
        self._items.move_to_end(new_id)
        return new_id, intake


_CHAT_STORE = _ChatSessionStore(_CHAT_MAX_SESSIONS, _CHAT_IDLE_TTL_S)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)
    # Which engine to answer this message on. Default GLM (typically cheaper);
    # GPT is opt-in via the frontend switcher. History is shared across a switch.
    model: Literal["glm", "gpt"] = "glm"


@app.post("/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    session_id, intake = _CHAT_STORE.get_or_create(req.session_id)

    # Pydantic AI is async-native, so the model turn awaits directly on the event
    # loop; only the (blocking) CP-SAT solve is offloaded to the shared
    # single-worker executor so chat and /solve can't stack solves on free tier.
    turn = await intake.send(req.message, model_key=req.model)

    session = intake.session
    rules = session.to_rules_json()
    try:
        outcome = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, solve_current, session)
        schedule = schedule_payload(outcome)
    except Exception:  # noqa: BLE001 -- a solve hiccup must not sink the chat reply
        schedule = {"status": "incomplete", "missing": [], "assumptions": [], "message": "No schedule yet."}

    return {"session_id": session_id, "reply": turn.text, "rules": rules, "schedule": schedule}


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE variant of ``/chat``: same request, but progress + token deltas land
    on the wire as the 20-40s turn happens instead of all at once at the end.

    Frozen contract (a second team builds the frontend against this exactly):
    zero or more ``status`` events (one per successful spec mutation, using the
    same dispatch echo text `/chat` folds into ``reply``, plus one "Solving your
    schedule..." right before the solve), then zero or more ``delta`` events
    (assistant reply token deltas), then exactly one terminal event -- ``final``
    on success or ``error`` on failure. See docs/DECISIONS.md D30.

    Bridge pattern: the turn runs as a task on the event loop; its callbacks --
    which Pydantic AI fires from a worker thread for tool calls (`on_progress`)
    and from the event loop for text deltas (`on_text_delta`) -- both hop onto
    the loop via `call_soon_threadsafe` into a queue, uniformly, since it is
    safe from either caller. The generator below drains that queue into SSE
    frames until a `None` sentinel closes the stream.
    """
    session_id, intake = _CHAT_STORE.get_or_create(req.session_id)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    def emit(event: str, data: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    def on_progress(text: str) -> None:
        emit("status", {"text": text})

    def on_text_delta(text: str) -> None:
        emit("delta", {"text": text})

    async def run_turn() -> None:
        try:
            turn = await intake.send(
                req.message,
                on_text_delta=on_text_delta,
                model_key=req.model,
                on_progress=on_progress,
            )

            session = intake.session
            rules = session.to_rules_json()
            emit("status", {"text": "Solving your schedule…"})
            try:
                outcome = await loop.run_in_executor(_EXECUTOR, solve_current, session)
                schedule = schedule_payload(outcome)
            except Exception:  # noqa: BLE001 -- a solve hiccup must not sink the chat reply
                schedule = {"status": "incomplete", "missing": [], "assumptions": [], "message": "No schedule yet."}

            emit("final", {"session_id": session_id, "reply": turn.text, "rules": rules, "schedule": schedule})
        except Exception:  # noqa: BLE001 -- any turn failure ends the stream with an error event, not a hang/500
            logger.exception("chat_stream turn failed")
            emit(
                "error",
                {"message": "Something went wrong handling that message. Your draft is safe — please send it again."},
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = asyncio.create_task(run_turn())

    async def event_gen() -> AsyncIterator[str]:
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield _sse_frame(event, data)
        finally:
            # Surface a bug in run_turn itself (outside its own try/except) as a
            # log entry rather than a silently swallowed task exception.
            if not task.done():
                task.cancel()
            with contextlib.suppress(Exception):
                await task

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/solve")
def solve_demo(req: DemoRequest) -> dict[str, Any]:
    total_teams = sum(d.teams for d in req.divisions)
    if total_teams > 64:
        raise HTTPException(status_code=422, detail="Demo caps at 64 teams total.")
    spec = _build_spec(req)
    future = _EXECUTOR.submit(_solve_and_shape, spec)
    try:
        return future.result(timeout=_REQUEST_TIMEOUT_S)
    except FuturesTimeoutError:
        raise HTTPException(status_code=504, detail="Solve timed out — try a smaller tournament.") from None
