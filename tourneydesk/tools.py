"""Spec-mutation tool suite (DESIGN.md sec 4).

`TOOLS` is the anthropic tool-definition list: strict JSON schema,
`additionalProperties: false`, full `required` lists (nullable types stand in
for "optional"), and a prescriptive `description` for when to call each tool.

`dispatch` is the single place tool calls turn into `SpecSession` mutations.
It never talks to the network and never raises on bad input from the model --
validation failures come back as an `is_error` ToolResult with an actionable
message the agent can read and correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from tourneydesk.session import SpecSession

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


# ---------------------------------------------------------------------------
# Shared schema fragments
# ---------------------------------------------------------------------------

_SOURCE_QUOTE_PROP = {
    "type": "string",
    "description": "The director's own words that justify this fact (verbatim or lightly trimmed).",
}

_TIME_WINDOW_SCHEMA = {
    "type": "object",
    "properties": {
        "start": {"type": "string", "description": "ISO 8601 datetime, e.g. 2026-09-12T08:00"},
        "end": {"type": "string", "description": "ISO 8601 datetime, e.g. 2026-09-12T18:00"},
    },
    "required": ["start", "end"],
    "additionalProperties": False,
}

_FIELD_SIZE_ENUM = ["small", "medium", "large", "full"]
_PRIORITY_ENUM = ["low", "medium", "high", "critical"]
_TARGET_TYPE_ENUM = ["team", "division"]


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": True,
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "set_tournament_info",
        "Call when the director states the tournament's name, or a description of the event. "
        "Safe to call again later if the name changes.",
        {
            "name": {"type": "string", "description": "The tournament's name."},
            "description": {
                "type": ["string", "null"],
                "description": "Free-text description of the tournament, or null if not stated.",
            },
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["name", "description", "source_quote"],
    ),
    _tool(
        "add_division",
        "Call when the director introduces a new age group / division (e.g. 'U10 Boys'). Provide "
        "field_size and game_duration_minutes if stated; leave other fields null if not stated -- "
        "they will be filled with labeled defaults later. Do not guess game_duration_minutes; ask "
        "if genuinely unknown.",
        {
            "id": {"type": "string", "description": "Short unique id, e.g. 'u10b'."},
            "name": {"type": "string", "description": "Human-readable division name, e.g. 'U10 Boys'."},
            "field_size": {
                "type": "string",
                "enum": _FIELD_SIZE_ENUM,
                "description": "Field size category this division plays on.",
            },
            "game_duration_minutes": {"type": "integer", "description": "Length of one game, in minutes."},
            "halftime_minutes": {"type": ["integer", "null"], "description": "Halftime length, or null if not stated."},
            "buffer_minutes": {
                "type": ["integer", "null"],
                "description": "Changeover buffer between games on the same field, or null if not stated.",
            },
            "min_rest_minutes": {
                "type": ["integer", "null"],
                "description": "Minimum rest between games for any team, or null if not stated.",
            },
            "games_per_team": {
                "type": ["integer", "null"],
                "description": "Pool-play games per team, or null if not stated.",
            },
            "pool_size": {"type": ["integer", "null"], "description": "Target teams per pool, or null if not stated."},
            "bracket_after_pools": {
                "type": ["boolean", "null"],
                "description": "Whether an elimination bracket follows pool play, or null if not stated.",
            },
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        [
            "id",
            "name",
            "field_size",
            "game_duration_minutes",
            "halftime_minutes",
            "buffer_minutes",
            "min_rest_minutes",
            "games_per_team",
            "pool_size",
            "bracket_after_pools",
            "source_quote",
        ],
    ),
    _tool(
        "update_division",
        "Call when the director corrects or adds detail to a division already added. Any field left "
        "null is left unchanged -- only pass the fields that are actually changing.",
        {
            "id": {"type": "string", "description": "Id of the division to update."},
            "name": {"type": ["string", "null"]},
            "field_size": {"type": ["string", "null"], "enum": [*_FIELD_SIZE_ENUM, None]},
            "game_duration_minutes": {"type": ["integer", "null"]},
            "halftime_minutes": {"type": ["integer", "null"]},
            "buffer_minutes": {"type": ["integer", "null"]},
            "min_rest_minutes": {"type": ["integer", "null"]},
            "games_per_team": {"type": ["integer", "null"]},
            "pool_size": {"type": ["integer", "null"]},
            "bracket_after_pools": {"type": ["boolean", "null"]},
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        [
            "id",
            "name",
            "field_size",
            "game_duration_minutes",
            "halftime_minutes",
            "buffer_minutes",
            "min_rest_minutes",
            "games_per_team",
            "pool_size",
            "bracket_after_pools",
            "source_quote",
        ],
    ),
    _tool(
        "remove_division",
        "Call when the director says a division no longer applies. Also removes that division's teams.",
        {"id": {"type": "string"}, "source_quote": _SOURCE_QUOTE_PROP},
        ["id", "source_quote"],
    ),
    _tool(
        "add_teams",
        "Call when the director names specific teams for a division. Omit a team's id to auto-derive "
        "one from its name.",
        {
            "division_id": {"type": "string", "description": "Id of the division these teams belong to."},
            "teams": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": ["string", "null"], "description": "Optional explicit team id."},
                        "name": {"type": "string", "description": "Team name."},
                        "club": {"type": ["string", "null"], "description": "Club/organization, if stated."},
                        "seed": {"type": ["integer", "null"], "description": "Seed (1 = highest), if stated."},
                    },
                    "required": ["id", "name", "club", "seed"],
                    "additionalProperties": False,
                },
            },
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["division_id", "teams", "source_quote"],
    ),
    _tool(
        "set_team_count",
        "Call when the director states a team COUNT for a division without naming the teams (e.g. "
        "'12 teams in U10'). Generates placeholder teams labeled as an assumption; prefer add_teams "
        "once real names are known.",
        {
            "division_id": {"type": "string"},
            "count": {"type": "integer", "description": "Total number of teams in this division."},
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["division_id", "count", "source_quote"],
    ),
    _tool(
        "add_field",
        "Call when the director introduces a new playing field with its size and availability window(s). "
        "Never fabricate a window -- ask if dates/times are unknown.",
        {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "size": {"type": "string", "enum": _FIELD_SIZE_ENUM},
            "availability": {
                "type": "array",
                "items": _TIME_WINDOW_SCHEMA,
                "description": "One or more windows the field is available, ISO 8601 datetimes.",
            },
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["id", "name", "size", "availability", "source_quote"],
    ),
    _tool(
        "set_field_availability",
        "Call when the director states or corrects a field's availability. Replaces all existing "
        "windows for that field.",
        {
            "field_id": {"type": "string"},
            "availability": {"type": "array", "items": _TIME_WINDOW_SCHEMA},
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["field_id", "availability", "source_quote"],
    ),
    _tool(
        "remove_field",
        "Call when the director says a field is no longer available for this tournament.",
        {"id": {"type": "string"}, "source_quote": _SOURCE_QUOTE_PROP},
        ["id", "source_quote"],
    ),
    _tool(
        "add_coaching_conflict",
        "Call when the director says one coach is responsible for two or more teams and so cannot be "
        "in two places at once.",
        {
            "coach_name": {"type": "string"},
            "team_ids": {"type": "array", "items": {"type": "string"}, "description": "At least two team ids."},
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["coach_name", "team_ids", "source_quote"],
    ),
    _tool(
        "remove_coaching_conflict",
        "Call when a previously stated coaching conflict no longer applies.",
        {"coach_name": {"type": "string"}, "source_quote": _SOURCE_QUOTE_PROP},
        ["coach_name", "source_quote"],
    ),
    _tool(
        "add_team_avoidance",
        "Call when the director says two specific teams should not play at the same time (e.g. same "
        "fan base, siblings on both rosters, rival schools).",
        {
            "team_ids": {"type": "array", "items": {"type": "string"}, "description": "Exactly two team ids."},
            "reason": {"type": ["string", "null"], "description": "Why, if stated."},
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["team_ids", "reason", "source_quote"],
    ),
    _tool(
        "remove_team_avoidance",
        "Call when a previously stated team avoidance no longer applies.",
        {
            "team_ids": {"type": "array", "items": {"type": "string"}},
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["team_ids", "source_quote"],
    ),
    _tool(
        "add_time_preference",
        "Call when the director expresses a preference (not a hard requirement) for a team or division "
        "to play within certain time windows.",
        {
            "target": {"type": "string", "description": "team_id or division_id this preference applies to."},
            "target_type": {"type": "string", "enum": _TARGET_TYPE_ENUM},
            "windows": {"type": "array", "items": _TIME_WINDOW_SCHEMA},
            "priority": {
                "type": ["string", "null"],
                "enum": [*_PRIORITY_ENUM, None],
                "description": "How strongly to weight this preference, or null to default to medium.",
            },
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["target", "target_type", "windows", "priority", "source_quote"],
    ),
    _tool(
        "add_field_preference",
        "Call when the director expresses a preference (not a hard requirement) for a team or division "
        "to play on specific fields.",
        {
            "target": {"type": "string", "description": "team_id or division_id this preference applies to."},
            "target_type": {"type": "string", "enum": _TARGET_TYPE_ENUM},
            "field_ids": {"type": "array", "items": {"type": "string"}},
            "priority": {
                "type": ["string", "null"],
                "enum": [*_PRIORITY_ENUM, None],
                "description": "How strongly to weight this preference, or null to default to low.",
            },
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["target", "target_type", "field_ids", "priority", "source_quote"],
    ),
    _tool(
        "get_spec_summary",
        "Call to review the full current draft before asking the director to confirm it, or whenever "
        "you need to re-orient on what has been captured so far. Takes no arguments.",
        {},
        [],
    ),
    _tool(
        "mark_intake_complete",
        "Call ONLY after calling get_spec_summary, reading the summary back to the director in plain "
        "language, and receiving their explicit confirmation that it is correct and complete.",
        {
            "confirmation_quote": {
                "type": "string",
                "description": "The director's own words confirming the summary is correct.",
            }
        },
        ["confirmation_quote"],
    ),
]

_TOOL_NAMES = {t["name"] for t in TOOLS}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _field_label(size: str) -> str:
    return {"small": "4v4/3v3", "medium": "7v7", "large": "9v9", "full": "11v11"}.get(size, size)


def _summarize(session: SpecSession) -> str:
    lines = [f"Tournament: {session.name or '(name not yet stated)'}"]
    if not session.divisions:
        lines.append("No divisions yet.")
    for d in session.divisions.values():
        team_count = sum(1 for t in session.teams.values() if t.division_id == d.id)
        lines.append(
            f"- {d.name} ({_field_label(d.field_size.value)}, {d.game_duration_minutes}-min games, "
            f"{team_count} team(s))"
        )
    if not session.fields:
        lines.append("No fields yet.")
    for f in session.fields.values():
        lines.append(f"- Field {f.name}: {len(f.availability)} availability window(s)")
    if session.coaching_conflicts:
        lines.append(f"{len(session.coaching_conflicts)} coaching conflict(s) recorded.")
    if session.team_avoidances:
        lines.append(f"{len(session.team_avoidances)} team avoidance(s) recorded.")
    try:
        _, assumptions = session.to_spec()
        if assumptions:
            lines.append("Assumptions that would be applied:")
            lines.extend(f"  - {a}" for a in assumptions)
        else:
            lines.append("Spec is complete with no outstanding assumptions.")
    except Exception as exc:  # IncompleteSpecError -- surfaced as plain text, not a crash
        lines.append(str(exc))
    return "\n".join(lines)


def dispatch(session: SpecSession, name: str, tool_input: dict[str, Any]) -> ToolResult:
    """Apply one tool call to `session`, returning a ToolResult.

    On success, `content` is a short plain-language echo confirmation. On a
    validation failure (bad enum value, out-of-range number, unknown
    reference, etc.) `content` is an actionable NL message and `is_error` is
    True -- the caller should feed this back to the model as a tool_result
    with is_error set, not raise.
    """
    if name not in _TOOL_NAMES:
        return ToolResult(content=f"Unknown tool '{name}'.", is_error=True)

    try:
        if name == "set_tournament_info":
            session.set_tournament_info(
                name=tool_input["name"],
                description=tool_input.get("description"),
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — tournament name set to '{session.name}'.")

        if name == "add_division":
            d = session.add_division(
                id=tool_input["id"],
                name=tool_input["name"],
                field_size=tool_input["field_size"],
                game_duration_minutes=tool_input["game_duration_minutes"],
                halftime_minutes=tool_input.get("halftime_minutes"),
                buffer_minutes=tool_input.get("buffer_minutes"),
                min_rest_minutes=tool_input.get("min_rest_minutes"),
                games_per_team=tool_input.get("games_per_team"),
                pool_size=tool_input.get("pool_size"),
                bracket_after_pools=tool_input.get("bracket_after_pools"),
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(
                f"Got it — added {d.name}, {_field_label(d.field_size.value)}, {d.game_duration_minutes}-min games."
            )

        if name == "update_division":
            d = session.update_division(
                id=tool_input["id"],
                name=tool_input.get("name"),
                field_size=tool_input.get("field_size"),
                game_duration_minutes=tool_input.get("game_duration_minutes"),
                halftime_minutes=tool_input.get("halftime_minutes"),
                buffer_minutes=tool_input.get("buffer_minutes"),
                min_rest_minutes=tool_input.get("min_rest_minutes"),
                games_per_team=tool_input.get("games_per_team"),
                pool_size=tool_input.get("pool_size"),
                bracket_after_pools=tool_input.get("bracket_after_pools"),
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — updated {d.name}.")

        if name == "remove_division":
            removed = session.remove_division(id=tool_input["id"], source_quote=tool_input["source_quote"])
            if not removed:
                return ToolResult(f"No division '{tool_input['id']}' to remove.", is_error=True)
            return ToolResult(f"Got it — removed division '{tool_input['id']}'.")

        if name == "add_teams":
            created = session.add_teams(
                division_id=tool_input["division_id"],
                teams=tool_input["teams"],
                source_quote=tool_input["source_quote"],
            )
            names = ", ".join(t.name for t in created)
            return ToolResult(f"Got it — added {len(created)} team(s): {names}.")

        if name == "set_team_count":
            created = session.set_team_count(
                division_id=tool_input["division_id"],
                count=tool_input["count"],
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — noted {len(created)} teams in this division (placeholder names for now).")

        if name == "add_field":
            f = session.add_field(
                id=tool_input["id"],
                name=tool_input["name"],
                size=tool_input["size"],
                availability=tool_input["availability"],
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(
                f"Got it — added field {f.name} ({_field_label(f.size.value)}) with "
                f"{len(f.availability)} availability window(s)."
            )

        if name == "set_field_availability":
            f = session.set_field_availability(
                field_id=tool_input["field_id"],
                availability=tool_input["availability"],
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — updated availability for field {f.name}.")

        if name == "remove_field":
            removed = session.remove_field(id=tool_input["id"], source_quote=tool_input["source_quote"])
            if not removed:
                return ToolResult(f"No field '{tool_input['id']}' to remove.", is_error=True)
            return ToolResult(f"Got it — removed field '{tool_input['id']}'.")

        if name == "add_coaching_conflict":
            c = session.add_coaching_conflict(
                coach_name=tool_input["coach_name"],
                team_ids=tool_input["team_ids"],
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — {c.coach_name} coaches {len(c.team_ids)} teams that can't overlap.")

        if name == "remove_coaching_conflict":
            removed = session.remove_coaching_conflict(
                coach_name=tool_input["coach_name"], source_quote=tool_input["source_quote"]
            )
            if not removed:
                return ToolResult(f"No coaching conflict for '{tool_input['coach_name']}' to remove.", is_error=True)
            return ToolResult(f"Got it — removed the coaching conflict for {tool_input['coach_name']}.")

        if name == "add_team_avoidance":
            a = session.add_team_avoidance(
                team_ids=tool_input["team_ids"],
                reason=tool_input.get("reason"),
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — {a.team_ids[0]} and {a.team_ids[1]} will not be scheduled simultaneously.")

        if name == "remove_team_avoidance":
            removed = session.remove_team_avoidance(
                team_ids=tool_input["team_ids"], source_quote=tool_input["source_quote"]
            )
            if not removed:
                return ToolResult("No matching team avoidance to remove.", is_error=True)
            return ToolResult("Got it — removed that team avoidance.")

        if name == "add_time_preference":
            session.add_time_preference(
                target=tool_input["target"],
                target_type=tool_input["target_type"],
                windows=tool_input["windows"],
                priority=tool_input.get("priority"),
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — noted a time preference for {tool_input['target']}.")

        if name == "add_field_preference":
            session.add_field_preference(
                target=tool_input["target"],
                target_type=tool_input["target_type"],
                field_ids=tool_input["field_ids"],
                priority=tool_input.get("priority"),
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — noted a field preference for {tool_input['target']}.")

        if name == "get_spec_summary":
            return ToolResult(_summarize(session))

        if name == "mark_intake_complete":
            session.mark_intake_complete(confirmation_quote=tool_input["confirmation_quote"])
            return ToolResult("Got it — intake marked complete.")

    except ValidationError as exc:
        return ToolResult(content=f"That doesn't fit the spec: {exc.errors()[0]['msg']}", is_error=True)
    except (ValueError, KeyError, LookupError) as exc:
        return ToolResult(content=str(exc), is_error=True)

    return ToolResult(content=f"Unhandled tool '{name}'.", is_error=True)
