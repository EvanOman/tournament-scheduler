"""Independent schedule validator.

Validates a solved schedule against all hard constraints, independent of the
solver.  This catches modeling bugs and provides confidence that the schedule
is correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from tournament_scheduler.models import TournamentSchedule, TournamentSpec


@dataclass
class ValidationResult:
    """Result of validating a schedule."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        lines = []
        if self.valid:
            lines.append("Schedule is VALID.")
        else:
            lines.append(f"Schedule has {len(self.errors)} error(s).")
        if self.warnings:
            lines.append(f"  {len(self.warnings)} warning(s).")
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)


def validate(schedule: TournamentSchedule, spec: TournamentSpec) -> ValidationResult:
    """Run all validation checks on a schedule."""
    result = ValidationResult()

    _check_no_field_double_booking(schedule, spec, result)
    _check_no_team_simultaneous_play(schedule, spec, result)
    _check_minimum_rest(schedule, spec, result)
    _check_field_size_match(schedule, spec, result)
    _check_all_matchups_scheduled(schedule, spec, result)
    _check_coaching_conflicts(schedule, spec, result)
    _check_field_availability(schedule, spec, result)
    _check_game_count_per_team(schedule, spec, result)

    return result


def _check_no_field_double_booking(
    schedule: TournamentSchedule,
    spec: TournamentSpec,
    result: ValidationResult,
) -> None:
    """Verify no field hosts two overlapping games."""
    field_ids = {f.id for f in spec.fields}
    for field_id in field_ids:
        games = schedule.games_on_field(field_id)
        for i, g1 in enumerate(games):
            for g2 in games[i + 1 :]:
                if g1.start_time < g2.end_time and g2.start_time < g1.end_time:
                    result.errors.append(
                        f"Field double-booking: {g1.game_id} ({g1.start_time:%H:%M}-{g1.end_time:%H:%M}) "
                        f"overlaps {g2.game_id} ({g2.start_time:%H:%M}-{g2.end_time:%H:%M}) on {field_id}"
                    )


def _check_no_team_simultaneous_play(
    schedule: TournamentSchedule,
    spec: TournamentSpec,
    result: ValidationResult,
) -> None:
    """Verify no team plays two overlapping games."""
    for team in spec.teams:
        games = schedule.games_for_team(team.id)
        for i, g1 in enumerate(games):
            for g2 in games[i + 1 :]:
                if g1.start_time < g2.end_time and g2.start_time < g1.end_time:
                    result.errors.append(
                        f"Team {team.id} plays simultaneously: {g1.game_id} "
                        f"({g1.start_time:%H:%M}) and {g2.game_id} ({g2.start_time:%H:%M})"
                    )


def _check_minimum_rest(
    schedule: TournamentSchedule,
    spec: TournamentSpec,
    result: ValidationResult,
) -> None:
    """Verify minimum rest between consecutive games for each team."""
    divisions_by_id = {d.id: d for d in spec.divisions}

    for team in spec.teams:
        division = divisions_by_id[team.division_id]
        min_rest = timedelta(minutes=division.min_rest_minutes)
        games = schedule.games_for_team(team.id)

        for i in range(len(games) - 1):
            g1 = games[i]
            g2 = games[i + 1]
            gap = g2.start_time - g1.end_time
            if gap < min_rest:
                result.errors.append(
                    f"Insufficient rest for {team.id}: {gap.total_seconds() / 60:.0f}min "
                    f"between {g1.game_id} (ends {g1.end_time:%H:%M}) and "
                    f"{g2.game_id} (starts {g2.start_time:%H:%M}), "
                    f"minimum is {division.min_rest_minutes}min"
                )


def _check_field_size_match(
    schedule: TournamentSchedule,
    spec: TournamentSpec,
    result: ValidationResult,
) -> None:
    """Verify games are played on appropriately-sized fields."""
    divisions_by_id = {d.id: d for d in spec.divisions}
    fields_by_id = {f.id: f for f in spec.fields}

    for game in schedule.games:
        division = divisions_by_id[game.division_id]
        field = fields_by_id[game.field_id]
        if field.size != division.field_size:
            result.errors.append(
                f"Field size mismatch: {game.game_id} in division {division.name} "
                f"(requires {division.field_size.value}) assigned to {field.name} "
                f"(size {field.size.value})"
            )


def _check_all_matchups_scheduled(
    schedule: TournamentSchedule,
    spec: TournamentSpec,
    result: ValidationResult,
) -> None:
    """Verify all required pool-play matchups appear in the schedule."""
    import itertools

    for pool in schedule.pools:
        expected_matchups = set(itertools.combinations(sorted(pool.team_ids), 2))
        actual_matchups = set()
        for game in schedule.games:
            if game.pool_id == pool.pool_id:
                pair = tuple(sorted([game.home_team_id, game.away_team_id]))
                actual_matchups.add(pair)

        division = next(d for d in spec.divisions if d.id == pool.division_id)
        n_teams = len(pool.team_ids)

        if division.games_per_team >= n_teams - 1:
            # Full round-robin expected
            missing = expected_matchups - actual_matchups
            for m in missing:
                result.errors.append(f"Missing matchup in {pool.pool_id}: {m[0]} vs {m[1]}")
        else:
            # Partial round-robin: just check game counts
            pass  # Handled by _check_game_count_per_team


def _check_coaching_conflicts(
    schedule: TournamentSchedule,
    spec: TournamentSpec,
    result: ValidationResult,
) -> None:
    """Verify no coaching conflicts (teams with shared coach playing simultaneously)."""
    for conflict in spec.coaching_conflicts:
        # Collect all games for all teams in this conflict
        all_games = []
        for tid in conflict.team_ids:
            all_games.extend([(tid, g) for g in schedule.games_for_team(tid)])

        # Check for overlaps between games of different teams
        for i, (tid1, g1) in enumerate(all_games):
            for tid2, g2 in all_games[i + 1 :]:
                if tid1 == tid2:
                    continue
                # Skip if they're the same game (teams play each other)
                if g1.game_id == g2.game_id:
                    continue
                if g1.start_time < g2.end_time and g2.start_time < g1.end_time:
                    result.errors.append(
                        f"Coaching conflict ({conflict.coach_name}): "
                        f"{tid1} game {g1.game_id} ({g1.start_time:%H:%M}) overlaps "
                        f"{tid2} game {g2.game_id} ({g2.start_time:%H:%M})"
                    )


def _check_field_availability(
    schedule: TournamentSchedule,
    spec: TournamentSpec,
    result: ValidationResult,
) -> None:
    """Verify games are within field availability windows."""
    fields_by_id = {f.id: f for f in spec.fields}

    for game in schedule.games:
        field = fields_by_id[game.field_id]
        in_window = False
        for window in field.availability:
            if game.start_time >= window.start and game.end_time <= window.end:
                in_window = True
                break
        if not in_window:
            result.errors.append(
                f"Game {game.game_id} on {field.name} ({game.start_time:%H:%M}-{game.end_time:%H:%M}) "
                f"is outside field availability windows"
            )


def _check_game_count_per_team(
    schedule: TournamentSchedule,
    spec: TournamentSpec,
    result: ValidationResult,
) -> None:
    """Verify each team plays the expected number of games."""
    divisions_by_id = {d.id: d for d in spec.divisions}

    for team in spec.teams:
        games = schedule.games_for_team(team.id)
        division = divisions_by_id[team.division_id]
        pool = next((p for p in schedule.pools if team.id in p.team_ids), None)
        if pool is None:
            result.errors.append(f"Team {team.id} is not in any pool")
            continue

        pool_size = len(pool.team_ids)
        expected_games = min(division.games_per_team, pool_size - 1)

        if len(games) != expected_games:
            result.warnings.append(f"Team {team.id} has {len(games)} games, expected {expected_games}")
