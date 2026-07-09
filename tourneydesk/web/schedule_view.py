"""Serialize a `SolveOutcome` into the UI-shaped JSON the Schedule panel renders.

The frontend needs two views of the same solve -- a per-field timeline grid and
a per-team itinerary -- plus enough layout metadata (the day's time span, a
colorblind-safe colour index per division) to draw them. Rather than ship raw
solver objects to the browser and rebuild names/offsets in TypeScript, we shape
one flat, self-describing payload here where the spec (team/field/division
names) is in hand. Every WebSocket `solve_completed` / `conflict_detected`
event and the REST `/schedule` endpoint emit exactly this.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tourneydesk.core.service import SolveOutcome


def _game_dict(
    game: Any,
    day_start: datetime,
    team_names: dict[str, str],
    field_names: dict[str, str],
    division_names: dict[str, str],
    division_color: dict[str, int],
) -> dict[str, Any]:
    return {
        "game_id": game.game_id,
        "division_id": game.division_id,
        "division_name": division_names.get(game.division_id, game.division_id),
        "color_index": division_color.get(game.division_id, 0),
        "pool_id": game.pool_id,
        "home_team_id": game.home_team_id,
        "away_team_id": game.away_team_id,
        "home": team_names.get(game.home_team_id, game.home_team_id),
        "away": team_names.get(game.away_team_id, game.away_team_id),
        "field_id": game.field_id,
        "field_name": field_names.get(game.field_id, game.field_id),
        "start": game.start_time.isoformat(),
        "end": game.end_time.isoformat(),
        "day": game.start_time.strftime("%a %b %d").replace(" 0", " "),
        "start_offset_min": int((game.start_time - day_start).total_seconds() // 60),
        "duration_min": int((game.end_time - game.start_time).total_seconds() // 60),
    }


def schedule_payload(outcome: SolveOutcome) -> dict[str, Any]:
    """Shape a `SolveOutcome` for the Schedule panel, for every solve status.

    ``incomplete`` -> a friendly "waiting for X" state (``missing`` populated).
    ``infeasible`` / ``invalid`` -> a conflict state the UI overlays in red.
    ``solved`` -> the full per-field + per-team grids.
    """
    base: dict[str, Any] = {
        "status": outcome.status,
        "missing": list(outcome.missing),
        "assumptions": list(outcome.assumptions),
    }

    if outcome.status == "incomplete":
        base["message"] = "Waiting on a few more details before a sample schedule can be drawn."
        return base

    if outcome.status == "inconclusive":
        base["message"] = (
            "This schedule is very tight — the quick solver pass ran out of time before "
            "deciding. Ask in chat what's making it hard, or simplify a constraint."
        )
        if outcome.schedule is not None:
            base["stats"] = outcome.schedule.stats.model_dump()
        return base

    schedule = outcome.schedule
    spec = outcome.spec
    if schedule is None or spec is None:
        base["message"] = "No schedule could be produced yet."
        return base

    team_names = {t.id: t.name for t in spec.teams}
    field_names = {f.id: f.name for f in spec.fields}
    division_names = {d.id: d.name for d in spec.divisions}
    # Colourblind-safe (Okabe-Ito) palette is applied on the frontend; here we
    # only assign each division a stable index in declaration order.
    division_color = {d.id: i for i, d in enumerate(spec.divisions)}

    if not schedule.games:
        base["message"] = "No games were scheduled."
        base["stats"] = schedule.stats.model_dump()
        return base

    day_start = min(g.start_time for g in schedule.games)
    day_end = max(g.end_time for g in schedule.games)
    games = [_game_dict(g, day_start, team_names, field_names, division_names, division_color) for g in schedule.games]

    fields_view = []
    for f in spec.fields:
        fg = sorted((g for g in games if g["field_id"] == f.id), key=lambda g: g["start"])
        fields_view.append({"id": f.id, "name": f.name, "size": f.size.value, "games": fg})

    teams_view = []
    for t in spec.teams:
        tg = sorted(
            (g for g in games if t.id in (g["home_team_id"], g["away_team_id"])),
            key=lambda g: g["start"],
        )
        teams_view.append(
            {
                "id": t.id,
                "name": t.name,
                "division_id": t.division_id,
                "division_name": division_names.get(t.division_id, t.division_id),
                "color_index": division_color.get(t.division_id, 0),
                "games": tg,
            }
        )

    base.update(
        {
            "tournament_name": schedule.tournament_name,
            "stats": schedule.stats.model_dump(),
            "day_start": day_start.isoformat(),
            "day_end": day_end.isoformat(),
            "total_min": int((day_end - day_start).total_seconds() // 60),
            "divisions": [{"id": d.id, "name": d.name, "color_index": division_color[d.id]} for d in spec.divisions],
            "fields": fields_view,
            "teams": teams_view,
        }
    )
    if outcome.validation is not None:
        base["validation"] = {
            "valid": outcome.validation.valid,
            "errors": list(outcome.validation.errors),
            "warnings": list(outcome.validation.warnings),
        }
    return base
