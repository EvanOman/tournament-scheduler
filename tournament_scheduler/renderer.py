"""Render schedules as human-readable output.

Supports markdown and HTML output formats:
- Per-field timeline: what's happening on each field across the day
- Per-team itinerary: each team's game schedule
- Per-division summary: pool standings and matchups
"""

from __future__ import annotations

from collections import defaultdict

from tournament_scheduler.models import TournamentSchedule, TournamentSpec


def render_markdown(schedule: TournamentSchedule, spec: TournamentSpec) -> str:
    """Render a schedule as a markdown document."""
    lines: list[str] = []
    teams_by_id = {t.id: t for t in spec.teams}
    fields_by_id = {f.id: f for f in spec.fields}
    divisions_by_id = {d.id: d for d in spec.divisions}

    lines.append(f"# {schedule.tournament_name}")
    lines.append("")
    lines.append(
        f"**Teams:** {schedule.stats.num_teams} | "
        f"**Fields:** {schedule.stats.num_fields} | "
        f"**Divisions:** {schedule.stats.num_divisions} | "
        f"**Games:** {schedule.stats.num_games_scheduled}"
    )
    lines.append(f"**Solver:** {schedule.stats.status} in {schedule.stats.wall_time_seconds:.1f}s")
    if schedule.stats.objective_value is not None:
        lines.append(f"**Objective:** {schedule.stats.objective_value:.0f}")
    lines.append("")

    # -- Pool assignments --
    lines.append("## Pools")
    lines.append("")
    for pool in sorted(schedule.pools, key=lambda p: p.pool_id):
        division = divisions_by_id[pool.division_id]
        lines.append(f"### {pool.pool_id} ({division.name})")
        lines.append("")
        for tid in pool.team_ids:
            team = teams_by_id[tid]
            seed_str = f" (seed {team.seed})" if team.seed else ""
            club_str = f" [{team.club}]" if team.club else ""
            lines.append(f"- {team.name}{seed_str}{club_str}")
        lines.append("")

    # -- Per-field timeline --
    lines.append("## Field Schedule")
    lines.append("")

    # Group games by date
    games_by_date: dict[str, list] = defaultdict(list)
    for game in schedule.games:
        date_key = game.start_time.strftime("%A, %B %d")
        games_by_date[date_key].append(game)

    for date_label in sorted(games_by_date.keys()):
        lines.append(f"### {date_label}")
        lines.append("")

        day_games = games_by_date[date_label]
        # Group by field
        field_games: dict[str, list] = defaultdict(list)
        for game in day_games:
            field_games[game.field_id].append(game)

        for field_id in sorted(field_games.keys()):
            field = fields_by_id[field_id]
            lines.append(f"#### {field.name}")
            lines.append("")
            lines.append("| Time | Division | Home | Away | Game |")
            lines.append("|------|----------|------|------|------|")

            for game in sorted(field_games[field_id], key=lambda g: g.start_time):
                home = teams_by_id[game.home_team_id]
                away = teams_by_id[game.away_team_id]
                div = divisions_by_id[game.division_id]
                time_str = f"{game.start_time:%H:%M}-{game.end_time:%H:%M}"
                lines.append(f"| {time_str} | {div.name} | {home.name} | {away.name} | {game.game_id} |")

            lines.append("")

    # -- Per-team itinerary --
    lines.append("## Team Itineraries")
    lines.append("")

    for division in sorted(spec.divisions, key=lambda d: d.name):
        lines.append(f"### {division.name}")
        lines.append("")

        div_teams = sorted(spec.teams_in_division(division.id), key=lambda t: t.name)
        for team in div_teams:
            games = schedule.games_for_team(team.id)
            if not games:
                continue

            lines.append(f"**{team.name}**")
            lines.append("")
            lines.append("| # | Time | Field | Opponent | Role |")
            lines.append("|---|------|-------|----------|------|")

            for i, game in enumerate(games, 1):
                if game.home_team_id == team.id:
                    opp = teams_by_id[game.away_team_id]
                    role = "Home"
                else:
                    opp = teams_by_id[game.home_team_id]
                    role = "Away"
                field = fields_by_id[game.field_id]
                time_str = f"{game.start_time:%H:%M}-{game.end_time:%H:%M}"
                lines.append(f"| {i} | {time_str} | {field.name} | {opp.name} | {role} |")

            lines.append("")

    return "\n".join(lines)


def render_html(schedule: TournamentSchedule, spec: TournamentSpec) -> str:
    """Render a schedule as a standalone HTML page."""
    teams_by_id = {t.id: t for t in spec.teams}
    fields_by_id = {f.id: f for f in spec.fields}
    divisions_by_id = {d.id: d for d in spec.divisions}

    # Build the field schedule table
    games_by_date: dict[str, list] = defaultdict(list)
    for game in schedule.games:
        date_key = game.start_time.strftime("%A, %B %d")
        games_by_date[date_key].append(game)

    field_sections = []
    for date_label in sorted(games_by_date.keys()):
        day_games = games_by_date[date_label]
        field_games: dict[str, list] = defaultdict(list)
        for game in day_games:
            field_games[game.field_id].append(game)

        field_tables = []
        for field_id in sorted(field_games.keys()):
            field = fields_by_id[field_id]
            rows = []
            for game in sorted(field_games[field_id], key=lambda g: g.start_time):
                home = teams_by_id[game.home_team_id]
                away = teams_by_id[game.away_team_id]
                div = divisions_by_id[game.division_id]
                rows.append(
                    f"<tr><td>{game.start_time:%H:%M}-{game.end_time:%H:%M}</td>"
                    f"<td>{div.name}</td>"
                    f"<td>{home.name}</td><td>{away.name}</td>"
                    f"<td>{game.game_id}</td></tr>"
                )
            field_tables.append(f"""
            <h4>{field.name}</h4>
            <div style="overflow-x:auto">
            <table>
            <tr><th>Time</th><th>Division</th><th>Home</th><th>Away</th><th>Game</th></tr>
            {"".join(rows)}
            </table>
            </div>""")

        field_sections.append(f"<h3>{date_label}</h3>{''.join(field_tables)}")

    # Build team itineraries
    team_sections = []
    for division in sorted(spec.divisions, key=lambda d: d.name):
        div_teams = sorted(spec.teams_in_division(division.id), key=lambda t: t.name)
        team_tables = []
        for team in div_teams:
            games = schedule.games_for_team(team.id)
            if not games:
                continue
            rows = []
            for i, game in enumerate(games, 1):
                if game.home_team_id == team.id:
                    opp = teams_by_id[game.away_team_id]
                    role = "Home"
                else:
                    opp = teams_by_id[game.home_team_id]
                    role = "Away"
                field = fields_by_id[game.field_id]
                rows.append(
                    f"<tr><td>{i}</td>"
                    f"<td>{game.start_time:%H:%M}-{game.end_time:%H:%M}</td>"
                    f"<td>{field.name}</td><td>{opp.name}</td><td>{role}</td></tr>"
                )
            team_tables.append(f"""
            <h4>{team.name}</h4>
            <div style="overflow-x:auto">
            <table>
            <tr><th>#</th><th>Time</th><th>Field</th><th>Opponent</th><th>Role</th></tr>
            {"".join(rows)}
            </table>
            </div>""")

        if team_tables:
            team_sections.append(f"<h3>{division.name}</h3>{''.join(team_tables)}")

    # Pool section
    pool_items = []
    for pool in sorted(schedule.pools, key=lambda p: p.pool_id):
        div = divisions_by_id[pool.division_id]
        team_list = "".join(
            f"<li>{teams_by_id[tid].name}{f' (seed {teams_by_id[tid].seed})' if teams_by_id[tid].seed else ''}</li>"
            for tid in pool.team_ids
        )
        pool_items.append(f"<h4>{pool.pool_id} ({div.name})</h4><ul>{team_list}</ul>")

    html = f"""<title>{schedule.tournament_name} - Schedule</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 1rem; color: #1a1a1a; }}
  h1 {{ border-bottom: 3px solid #2563eb; padding-bottom: 0.5rem; }}
  h2 {{ color: #2563eb; margin-top: 2rem; }}
  h3 {{ color: #374151; }}
  h4 {{ color: #6b7280; margin-bottom: 0.25rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1rem; }}
  th, td {{ border: 1px solid #d1d5db; padding: 0.4rem 0.75rem; text-align: left; }}
  th {{ background: #f3f4f6; font-weight: 600; }}
  tr:nth-child(even) {{ background: #f9fafb; }}
  .stats {{ background: #eff6ff; border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem; }}
  .stats span {{ margin-right: 1.5rem; }}
  ul {{ padding-left: 1.25rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #111827; color: #e5e7eb; }}
    h1 {{ border-color: #3b82f6; }}
    h2 {{ color: #60a5fa; }}
    h3 {{ color: #9ca3af; }}
    h4 {{ color: #6b7280; }}
    th {{ background: #1f2937; }}
    td {{ border-color: #374151; }}
    tr:nth-child(even) {{ background: #1f2937; }}
    .stats {{ background: #1e3a5f; }}
  }}
</style>

<h1>{schedule.tournament_name}</h1>
<div class="stats">
  <span><strong>Teams:</strong> {schedule.stats.num_teams}</span>
  <span><strong>Fields:</strong> {schedule.stats.num_fields}</span>
  <span><strong>Divisions:</strong> {schedule.stats.num_divisions}</span>
  <span><strong>Games:</strong> {schedule.stats.num_games_scheduled}</span>
  <span><strong>Solver:</strong> {schedule.stats.status} in {schedule.stats.wall_time_seconds:.1f}s</span>
</div>

<h2>Pools</h2>
{"".join(pool_items)}

<h2>Field Schedule</h2>
{"".join(field_sections)}

<h2>Team Itineraries</h2>
{"".join(team_sections)}
"""
    return html
