"""End-to-end: chat -> tools -> spec -> solve -> validate, with zero network.

Drives a FakeIntake through the IntakeService (the same service class the CLI
and the future web app both use) via run_conversation with a FakePersona,
then proves the resulting SpecSession materializes into a TournamentSpec that
assign_pools -> solve -> validate accepts as VALID.
"""

from __future__ import annotations

import asyncio

from tournament_scheduler.pools import assign_pools
from tournament_scheduler.solver import solve
from tournament_scheduler.validator import validate
from tourneydesk.core import IntakeService
from tourneydesk.persona import FakePersona
from tourneydesk.providers.fake import FakeIntake
from tourneydesk.session import SpecSession

_AVAILABILITY = [{"start": "2026-09-12T08:00", "end": "2026-09-12T18:00"}]


def _team(name: str) -> dict[str, str | None]:
    return {"id": None, "name": name, "club": None, "seed": None}


SCRIPT: list[dict[str, object]] = [
    {
        "tool_calls": [
            {
                "name": "set_tournament_info",
                "input": {
                    "name": "Fall Classic",
                    "description": "A small two-division fall tournament",
                    "source_quote": "We're running the Fall Classic",
                },
            },
            {
                "name": "add_division",
                "input": {
                    "id": "u10b",
                    "name": "U10 Boys",
                    "field_size": "medium",
                    "game_duration_minutes": 25,
                    "halftime_minutes": 5,
                    "buffer_minutes": 10,
                    "min_rest_minutes": 45,
                    "games_per_team": 3,
                    "pool_size": 4,
                    "bracket_after_pools": None,
                    "source_quote": "U10 Boys play 7v7, 25 minute games, 5 min half, 3 games each",
                },
            },
            {
                "name": "add_division",
                "input": {
                    "id": "u12g",
                    "name": "U12 Girls",
                    "field_size": "large",
                    "game_duration_minutes": 30,
                    "halftime_minutes": 5,
                    "buffer_minutes": 10,
                    "min_rest_minutes": 50,
                    "games_per_team": 3,
                    "pool_size": 4,
                    "bracket_after_pools": None,
                    "source_quote": "U12 Girls play 9v9, 30 minute games",
                },
            },
        ],
        "text": "Got it -- Fall Classic with U10 Boys (7v7) and U12 Girls (9v9).",
    },
    {
        "tool_calls": [
            {
                "name": "add_teams",
                "input": {
                    "division_id": "u10b",
                    "teams": [_team("Atlas FC"), _team("Storm SC"), _team("FC Thunder"), _team("Eclipse FC")],
                    "source_quote": "U10 Boys teams: Atlas FC, Storm SC, FC Thunder, Eclipse FC",
                },
            }
        ],
        "text": "Got it -- four U10 Boys teams recorded.",
    },
    {
        "tool_calls": [
            {
                "name": "add_teams",
                "input": {
                    "division_id": "u12g",
                    "teams": [_team("Dynamo SC"), _team("Capital FC"), _team("Phoenix FC"), _team("Sporting SC")],
                    "source_quote": "U12 Girls teams: Dynamo SC, Capital FC, Phoenix FC, Sporting SC",
                },
            }
        ],
        "text": "Got it -- four U12 Girls teams recorded.",
    },
    {
        "tool_calls": [
            {
                "name": "add_field",
                "input": {
                    "id": "f1",
                    "name": "Field 1",
                    "size": "medium",
                    "availability": _AVAILABILITY,
                    "source_quote": "Field 1 is open 8am to 6pm on the 12th",
                },
            },
            {
                "name": "add_field",
                "input": {
                    "id": "f2",
                    "name": "Field 2",
                    "size": "large",
                    "availability": _AVAILABILITY,
                    "source_quote": "Field 2 is also open 8am to 6pm on the 12th",
                },
            },
        ],
        "text": "Got it -- two fields recorded.",
    },
    {
        "tool_calls": [{"name": "get_spec_summary", "input": {}}],
        "text": "Here's what I have so far: [summary]. Does that look right?",
    },
    {
        "tool_calls": [
            {"name": "mark_intake_complete", "input": {"confirmation_quote": "Yep, that's exactly right, go ahead"}}
        ],
        "text": "Great, I've got everything I need.",
    },
]

DIRECTOR_LINES = [
    "We're running the Fall Classic. U10 Boys plays 7v7, 25 minute games. U12 Girls plays 9v9, 30 minute games.",
    "U10 Boys teams are Atlas FC, Storm SC, FC Thunder, and Eclipse FC.",
    "U12 Girls teams are Dynamo SC, Capital FC, Phoenix FC, and Sporting SC.",
    "We have two fields, both open 8am to 6pm on September 12th.",
    "That's everything I think.",
    "Yep, that's exactly right, go ahead.",
]


def test_fake_intake_end_to_end_produces_valid_schedule():
    session = SpecSession()
    provider = FakeIntake(session, SCRIPT)
    persona = FakePersona(DIRECTOR_LINES)
    service = IntakeService(provider)

    result_session = asyncio.run(service.run_conversation(persona, max_turns=20))

    assert result_session is session
    assert session.intake_complete is True

    spec, assumptions = session.to_spec()
    assert len(spec.divisions) == 2
    assert len(spec.teams) == 8
    assert len(spec.fields) == 2
    assert assumptions == []  # every field was explicitly stated in the script

    pools = assign_pools(spec)
    schedule = solve(spec, pools)
    assert schedule.stats.status in ("OPTIMAL", "FEASIBLE")

    result = validate(schedule, spec)
    assert result.valid, result.summary()


def test_try_solve_via_service_matches_manual_pipeline():
    session = SpecSession()
    provider = FakeIntake(session, list(SCRIPT))
    persona = FakePersona(list(DIRECTOR_LINES))
    service = IntakeService(provider)

    asyncio.run(service.run_conversation(persona, max_turns=20))

    outcome = service.try_solve()
    assert outcome.status == "solved"
    assert outcome.ok
    assert outcome.schedule is not None
    assert outcome.validation is not None
    assert outcome.validation.valid


def test_try_solve_reports_incomplete_before_intake_finishes():
    session = SpecSession()
    provider = FakeIntake(session, SCRIPT[:1])
    persona = FakePersona(DIRECTOR_LINES[:1])
    service = IntakeService(provider)

    asyncio.run(service.run_conversation(persona, max_turns=20))

    outcome = service.try_solve()
    assert outcome.status == "incomplete"
    assert outcome.missing
