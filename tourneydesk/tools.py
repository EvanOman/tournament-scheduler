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


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    # Strict compilation is off for the whole suite: 17 tools with nullable unions
    # exceed the API's compiled-grammar budget (16-union cap, then overall grammar
    # size). dispatch() validates every input locally and returns is_error results
    # the model can correct, which covers what strict would have guaranteed.
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": strict,
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
        "if genuinely unknown. field_size describes the PHYSICAL FIELD the division needs (it "
        "gates which fields the solver may use); game_format records the playing format (e.g. "
        "'8v8') VERBATIM as stated and is never derived from field size.",
        {
            "id": {"type": "string", "description": "Short unique id, e.g. 'u10b'."},
            "name": {"type": "string", "description": "Human-readable division name, e.g. 'U10 Boys'."},
            "field_size": {
                "type": "string",
                "enum": _FIELD_SIZE_ENUM,
                "description": "Physical field size category this division plays on (gates field eligibility).",
            },
            "game_format": {
                "type": ["string", "null"],
                "description": "Playing format exactly as the director stated it (e.g. '8v8', '7v7'), "
                "or null if not stated. Never infer this from field size.",
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
            "game_format",
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
            "field_size": {"anyOf": [{"type": "string", "enum": _FIELD_SIZE_ENUM}, {"type": "null"}]},
            "game_format": {
                "type": ["string", "null"],
                "description": "Playing format verbatim (e.g. '8v8'), or null to leave unchanged.",
            },
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
            "game_format",
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
                "anyOf": [{"type": "string", "enum": _PRIORITY_ENUM}, {"type": "null"}],
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
                "anyOf": [{"type": "string", "enum": _PRIORITY_ENUM}, {"type": "null"}],
                "description": "How strongly to weight this preference, or null to default to low.",
            },
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["target", "target_type", "field_ids", "priority", "source_quote"],
    ),
    _tool(
        "remove_time_preference",
        "Call to withdraw the time preference(s) recorded for a team or division — when the "
        "director retracts one, or when a preference YOU derived turns out wrong or unconfirmed.",
        {
            "target": {"type": "string", "description": "team_id or division_id whose time preferences to remove."},
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["target", "source_quote"],
    ),
    _tool(
        "remove_field_preference",
        "Call to withdraw the field preference(s) recorded for a team or division — when the "
        "director retracts one, or when a preference YOU derived turns out wrong or unconfirmed.",
        {
            "target": {"type": "string", "description": "team_id or division_id whose field preferences to remove."},
            "source_quote": _SOURCE_QUOTE_PROP,
        },
        ["target", "source_quote"],
    ),
    _tool(
        "get_spec_summary",
        "Call to review the full current draft before asking the director to confirm it, or whenever "
        "you need to re-orient on what has been captured so far. Takes no arguments.",
        {},
        [],
    ),
    _tool(
        "get_schedule_summary",
        "Call BEFORE answering ANY question about the current sample schedule — why a team plays "
        "when it does, how games are spread across fields or days, whether a recent change actually "
        "took effect, or whether two games conflict. Runs the solver on the current draft and "
        "returns the real schedule state. NEVER guess, speculate, or claim the preview is stale: "
        "this tool is your ground truth. Takes no arguments.",
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


def _schedule_digest(session: SpecSession) -> str:
    """Return a compact, factual digest of the CURRENT schedule.

    Ground truth for the agent's schedule answers (persona P5 caught it
    confabulating about state it could not see). Goes through the same
    memoized solve as the UI panel, so agent and panel always describe the
    SAME solution; includes game-level matchups so the agent never has to
    assert pairing facts it cannot see.
    """
    from tourneydesk.core.service import solve_current  # noqa: PLC0415 -- avoids tools<->core import cycle

    outcome = solve_current(session)
    if outcome.status == "incomplete":
        return "No schedule yet — the draft is missing:\n" + "\n".join(f"  - {m}" for m in outcome.missing)
    if outcome.status == "infeasible":
        return "Current draft is INFEASIBLE — no schedule satisfies all hard constraints as stated."
    if outcome.status == "inconclusive":
        return (
            "The quick solve pass timed out UNDECIDED — the draft is very tightly constrained. "
            "Neither a schedule nor a proof of impossibility yet."
        )

    schedule = outcome.schedule
    spec = outcome.spec
    result = outcome.validation
    assert schedule is not None and spec is not None  # solved/invalid always carry both
    team_names = {t.id: t.name for t in spec.teams}
    lines = [
        f"Schedule status: {schedule.stats.status} ({schedule.stats.wall_time_seconds:.1f}s), "
        f"{len(schedule.games)} games, validator "
        f"{'PASSED' if result is not None and result.valid else 'FAILED'}. "
        "This is the exact schedule the director's panel shows."
    ]
    assumptions = outcome.assumptions
    if assumptions:
        lines.append("Applied assumptions: " + "; ".join(assumptions))

    by_field: dict[str, list[Any]] = {f.id: [] for f in spec.fields}
    for g in schedule.games:
        by_field.setdefault(g.field_id, []).append(g)
    field_names = {f.id: f.name for f in spec.fields}
    lines.append("Per field:")
    for fid, games in by_field.items():
        if not games:
            lines.append(f"  - {field_names.get(fid, fid)}: NO GAMES ASSIGNED")
            continue
        days = sorted({g.start_time.strftime("%a") for g in games})
        first = min(g.start_time for g in games).strftime("%a %H:%M")
        last = max(g.end_time for g in games).strftime("%a %H:%M")
        lines.append(f"  - {field_names.get(fid, fid)}: {len(games)} games, days {'/'.join(days)}, {first}–{last}")

    field_name = {f.id: f.name for f in spec.fields}
    lines.append("Per team (every game — day, time, opponent, field):")
    by_team_games: dict[str, list[Any]] = {}
    for g in schedule.games:
        by_team_games.setdefault(g.home_team_id, []).append(g)
        by_team_games.setdefault(g.away_team_id, []).append(g)
    for tid, games in by_team_games.items():
        games.sort(key=lambda g: g.start_time)
        parts = []
        for g in games:
            opp = g.away_team_id if g.home_team_id == tid else g.home_team_id
            when = g.start_time.strftime("%a %H:%M")
            parts.append(f"{when} v {team_names.get(opp, opp)} ({field_name.get(g.field_id, g.field_id)})")
        lines.append(f"  - {team_names.get(tid, tid)}: {'; '.join(parts)}")
    return "\n".join(lines)


def _division_label(d: Any) -> str:
    # Never gloss field size as a playing format (an auto-derived "7v7" once
    # overwrote a director's explicit "8v8" — persona finding, DECISIONS D15/D16).
    fmt = f"{d.game_format}, " if d.game_format else ""
    return f"{fmt}{d.field_size.value} fields"


def _summarize(session: SpecSession) -> str:
    lines = [f"Tournament: {session.name or '(name not yet stated)'}"]
    if not session.divisions:
        lines.append("No divisions yet.")
    for d in session.divisions.values():
        team_count = sum(1 for t in session.teams.values() if t.division_id == d.id)
        lines.append(f"- {d.name} ({_division_label(d)}, {d.game_duration_minutes}-min games, {team_count} team(s))")
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
                game_format=tool_input.get("game_format"),
                game_duration_minutes=tool_input["game_duration_minutes"],
                halftime_minutes=tool_input.get("halftime_minutes"),
                buffer_minutes=tool_input.get("buffer_minutes"),
                min_rest_minutes=tool_input.get("min_rest_minutes"),
                games_per_team=tool_input.get("games_per_team"),
                pool_size=tool_input.get("pool_size"),
                bracket_after_pools=tool_input.get("bracket_after_pools"),
                source_quote=tool_input["source_quote"],
            )
            return ToolResult(f"Got it — added {d.name}, {_division_label(d)}, {d.game_duration_minutes}-min games.")

        if name == "update_division":
            d = session.update_division(
                id=tool_input["id"],
                name=tool_input.get("name"),
                field_size=tool_input.get("field_size"),
                game_format=tool_input.get("game_format"),
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
                f"Got it — added field {f.name} ({f.size.value}-size) with "
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

        if name == "remove_time_preference":
            n = session.remove_time_preferences(target=tool_input["target"], source_quote=tool_input["source_quote"])
            if n == 0:
                return ToolResult(f"No time preferences recorded for '{tool_input['target']}'.", is_error=True)
            return ToolResult(f"Got it — removed {n} time preference(s) for {tool_input['target']}.")

        if name == "remove_field_preference":
            n = session.remove_field_preferences(target=tool_input["target"], source_quote=tool_input["source_quote"])
            if n == 0:
                return ToolResult(f"No field preferences recorded for '{tool_input['target']}'.", is_error=True)
            return ToolResult(f"Got it — removed {n} field preference(s) for {tool_input['target']}.")

        if name == "get_spec_summary":
            return ToolResult(_summarize(session))

        if name == "get_schedule_summary":
            return ToolResult(_schedule_digest(session))

        if name == "mark_intake_complete":
            session.mark_intake_complete(confirmation_quote=tool_input["confirmation_quote"])
            return ToolResult("Got it — intake marked complete.")

    except ValidationError as exc:
        return ToolResult(content=f"That doesn't fit the spec: {exc.errors()[0]['msg']}", is_error=True)
    except KeyError as exc:
        # Tools are non-strict, so the model can omit a required argument; str(KeyError)
        # is just the bare key repr, which is useless (and once leaked into the UI).
        return ToolResult(
            content=(
                f"Tool '{name}' was called without its required argument {exc}. "
                "Call it again with every declared field (pass null for unstated optional fields)."
            ),
            is_error=True,
        )
    except (ValueError, LookupError) as exc:
        return ToolResult(content=str(exc), is_error=True)

    return ToolResult(content=f"Unhandled tool '{name}'.", is_error=True)
