"""Bound public-demo work that happens before CP-SAT's solve timer starts.

The current solver scans its decision variables per slot and repeatedly per
team/game pair; it also visits Cartesian products of feasible slots. Entity
caps alone therefore do not bound model construction tightly enough. This
guard counts slots arithmetically and bounds partial round-robin degrees
without constructing slots, matchups, or a CP model.

Call ``validate_public_solver_work(spec, pools)`` after ``assign_pools`` and
before any public solve or conflict extraction. Ordinary product/CLI solves
do not opt into this public-demo budget.
"""

from __future__ import annotations

from datetime import timedelta

from tournament_scheduler.models import FieldSize, Pool, TournamentSpec
from tourneydesk.limits import PUBLIC_DEMO_LIMITS

MAX_PUBLIC_SLOTS = 512
MAX_PUBLIC_MATCHUPS = 256
MAX_PUBLIC_DECISION_VARIABLES = 10_000
MAX_PUBLIC_VARIABLE_SCANS = 2_000_000
MAX_PUBLIC_PAIR_ITERATIONS = 2_000_000
# The published packed-weekend preset needs about 5.3M of these lightweight
# scans because the existing soft objective filters x inside its slot loop.
MAX_PUBLIC_SOFT_SCANS = 8_000_000
MAX_PUBLIC_PREFERENCE_VARIABLES = 10_000

_WORKLOAD_MESSAGE = "Demo workload too large; reduce teams, fields or availability."


def _check_budget(estimate: int, maximum: int) -> None:
    if estimate > maximum:
        raise ValueError(_WORKLOAD_MESSAGE)


def validate_public_solver_work(spec: TournamentSpec, pools: list[Pool]) -> None:
    """Reject excessive public model-building work using conservative counts.

    All collections are capped before walking their members. The estimates
    include duplicate availability windows, field-size compatibility, odd
    partial round robins, shared coaches, and time-preference scans. They can
    reject some shapes that would build more cheaply; they never enumerate
    a potentially large slot or game-pair product to make that decision.
    """
    limits = PUBLIC_DEMO_LIMITS
    _check_budget(len(spec.teams), limits.max_total_teams)
    _check_budget(len(spec.divisions), limits.max_divisions)
    _check_budget(len(spec.fields), limits.max_fields)
    _check_budget(len(pools), limits.max_total_teams)
    _check_budget(len(spec.coaching_conflicts), limits.max_coaching_conflicts)
    _check_budget(
        sum(
            len(items)
            for items in (spec.coaching_conflicts, spec.team_avoidances, spec.time_preferences, spec.field_preferences)
        ),
        limits.max_relationships,
    )
    for pool in pools:
        _check_budget(len(pool.team_ids), 8)  # the domain's maximum pool size
    for field in spec.fields:
        _check_budget(len(field.availability), limits.max_windows_per_fact)
    for conflict in spec.coaching_conflicts:
        _check_budget(len(conflict.team_ids), limits.max_teams_per_coach)
    for preference in spec.time_preferences:
        _check_budget(len(preference.preferred_windows), limits.max_windows_per_fact)

    divisions = {division.id: division for division in spec.divisions}
    teams = {team.id: team for team in spec.teams}
    slots_by_size: dict[FieldSize, int] = {}
    slots = 0
    for field in spec.fields:
        compatible = [division for division in spec.divisions if division.field_size == field.size]
        if not compatible:
            continue
        slot_duration = timedelta(minutes=max(spec.total_game_minutes(division) for division in compatible))
        # This is exactly the count from _build_time_slots' while loop, with
        # no slot objects or iteration over the duration of the event.
        field_slots = sum((window.end - window.start) // slot_duration for window in field.availability)
        slots += field_slots
        _check_budget(slots, MAX_PUBLIC_SLOTS)
        slots_by_size[field.size] = slots_by_size.get(field.size, 0) + field_slots
    feasible_slots = {id: slots_by_size.get(division.field_size, 0) for id, division in divisions.items()}

    matchups_by_division = dict.fromkeys(divisions, 0)
    team_degrees = dict.fromkeys(teams, 0)
    seen_teams: set[str] = set()
    for pool in pools:
        if pool.division_id not in divisions:
            raise ValueError(_WORKLOAD_MESSAGE)
        for team_id in pool.team_ids:
            if team_id not in teams or teams[team_id].division_id != pool.division_id or team_id in seen_teams:
                raise ValueError(_WORKLOAD_MESSAGE)
            seen_teams.add(team_id)
        count = len(pool.team_ids)
        if count < 2:
            continue
        games = divisions[pool.division_id].games_per_team
        if games >= count - 1:
            pool_matchups = count * (count - 1) // 2
            degree = count - 1
        else:
            # The circle method gives each even pool one game per round.
            # In an odd pool, one extra round covers every team's bye; some
            # teams can consequently play one more than games_per_team.
            rounds = games + count % 2
            pool_matchups = rounds * (count // 2)
            degree = min(rounds, count - 1)
        matchups_by_division[pool.division_id] += pool_matchups
        for team_id in pool.team_ids:
            team_degrees[team_id] += degree

    matchups = sum(matchups_by_division.values())
    _check_budget(matchups, MAX_PUBLIC_MATCHUPS)
    variables = sum(count * feasible_slots[id] for id, count in matchups_by_division.items())
    _check_budget(variables, MAX_PUBLIC_DECISION_VARIABLES)
    # Initial compatibility candidates, then a full x scan for every slot.
    _check_budget(matchups * slots + slots * variables, MAX_PUBLIC_VARIABLE_SCANS)

    pair_iterations = slots * slots  # _compute_slot_overlaps visits all pairs
    soft_scans = sum(team_degrees.values()) * variables  # early/late objective
    for team_id, degree in team_degrees.items():
        division_id = teams[team_id].division_id
        feasible = feasible_slots[division_id]
        pairs = degree * (degree - 1) // 2
        # Simultaneous games and the back-to-back objective always run; the
        # rest pass runs only when a positive minimum rest was requested.
        passes = 2 + (divisions[division_id].min_rest_minutes > 0)
        pair_iterations += passes * pairs * feasible * feasible
        # The soft pair loop filters all x once for m1, then once for m2
        # for every feasible s1, before testing its Cartesian slot product.
        soft_scans += pairs * (1 + feasible) * variables

    for conflict in spec.coaching_conflicts:
        coached_teams = set(conflict.team_ids)
        affected = min(matchups, sum(team_degrees[team_id] for team_id in coached_teams))
        feasible = max((feasible_slots[teams[team_id].division_id] for team_id in coached_teams), default=0)
        pair_iterations += affected * (affected - 1) // 2 * feasible * feasible
    _check_budget(pair_iterations, MAX_PUBLIC_PAIR_ITERATIONS)

    preference_variables = 0
    for preference in spec.time_preferences:
        if preference.target_type == "division":
            targets = [team.id for team in spec.teams if team.division_id == preference.target]
        else:
            targets = [preference.target] if preference.target in teams else []
        for team_id in targets:
            appearances = team_degrees[team_id]
            applications = appearances * feasible_slots[teams[team_id].division_id]
            soft_scans += appearances * variables + applications * (1 + len(preference.preferred_windows))
            preference_variables += applications
    _check_budget(preference_variables, MAX_PUBLIC_PREFERENCE_VARIABLES)
    _check_budget(soft_scans, MAX_PUBLIC_SOFT_SCANS)
