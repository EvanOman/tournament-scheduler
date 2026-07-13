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

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
from tourneydesk.core.service import SolveOutcome
from tourneydesk.explain.engine import explain_conflict
from tourneydesk.web.schedule_view import schedule_payload

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
            explanation = explain_conflict(spec, conflict, use_llm=False)
            payload["explanation"] = explanation.model_dump(mode="json")
        return payload

    return {"result": "inconclusive"}


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
