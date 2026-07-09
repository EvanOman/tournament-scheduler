"""A canned FakeIntake script for offline demos and verification.

`serve --provider fake` wires each new session to a FakeIntake driven by this
script, so the full stack (WebSocket streaming -> spec mutation -> spec_updated
-> debounced speculative solve -> solve_completed with a real schedule) can be
exercised with zero network and no API key. FakeIntake ignores the director's
actual words and simply walks these turns, so any six messages the user types
drive the tournament to completion -- ideal for a browser walk-through.
"""

from __future__ import annotations

from typing import Any

_AVAIL = [{"start": "2026-09-12T08:00", "end": "2026-09-12T18:00"}]


def _team(name: str) -> dict[str, Any]:
    return {"id": None, "name": name, "club": None, "seed": None}


CANNED_SCRIPT: list[dict[str, Any]] = [
    {
        "tool_calls": [
            {
                "name": "set_tournament_info",
                "input": {
                    "name": "Fall Classic",
                    "description": "A two-division fall tournament",
                    "source_quote": "We're running the Fall Classic this September.",
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
                    "source_quote": "U10 Boys play 7v7, 25-minute games, three each.",
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
                    "source_quote": "U12 Girls play 9v9, 30-minute games.",
                },
            },
        ],
        "text": "Got it -- the Fall Classic with two divisions: U10 Boys (7v7) and U12 Girls (9v9). "
        "Who's playing in U10 Boys?",
    },
    {
        "tool_calls": [
            {
                "name": "add_teams",
                "input": {
                    "division_id": "u10b",
                    "teams": [_team("Atlas FC"), _team("Storm SC"), _team("FC Thunder"), _team("Eclipse FC")],
                    "source_quote": "U10 Boys: Atlas FC, Storm SC, FC Thunder, Eclipse FC.",
                },
            }
        ],
        "text": "Four U10 Boys teams recorded. And the U12 Girls?",
    },
    {
        "tool_calls": [
            {
                "name": "add_teams",
                "input": {
                    "division_id": "u12g",
                    "teams": [_team("Dynamo SC"), _team("Capital FC"), _team("Phoenix FC"), _team("Sporting SC")],
                    "source_quote": "U12 Girls: Dynamo SC, Capital FC, Phoenix FC, Sporting SC.",
                },
            }
        ],
        "text": "Four U12 Girls teams recorded. How many fields do you have, and when are they open?",
    },
    {
        "tool_calls": [
            {
                "name": "add_field",
                "input": {
                    "id": "f1",
                    "name": "Field 1",
                    "size": "medium",
                    "availability": _AVAIL,
                    "source_quote": "Field 1 is open 8am to 6pm on the 12th.",
                },
            },
            {
                "name": "add_field",
                "input": {
                    "id": "f2",
                    "name": "Field 2",
                    "size": "large",
                    "availability": _AVAIL,
                    "source_quote": "Field 2 is also open 8am to 6pm on the 12th.",
                },
            },
        ],
        "text": "Two fields, both open 8am-6pm on the 12th. Let me read back the full plan.",
    },
    {
        "tool_calls": [{"name": "get_spec_summary", "input": {}}],
        "text": "Here's everything I have -- two divisions, eight teams, two fields, all day Saturday. "
        "Does that look right?",
    },
    {
        "tool_calls": [{"name": "mark_intake_complete", "input": {"confirmation_quote": "Yep, that's exactly right."}}],
        "text": "Great -- I've got everything I need. Your sample schedule is on the right.",
    },
]
