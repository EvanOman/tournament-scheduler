"""Generate realistic synthetic tournament fixtures for testing.

Based on research findings:
- Tier 2 regional soccer tournaments: 40-150 teams
- Weekend format: Saturday + Sunday
- Multiple age groups / divisions
- 4-8 fields of various sizes
- Pool play (3-4 games per team) + optional bracket

Three test fixtures at increasing scale:
1. Small: 24 teams, 3 divisions, 4 fields, single day
2. Medium: 48 teams, 5 divisions, 6 fields, weekend
3. Large: 96 teams, 8 divisions, 10 fields, weekend
"""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

from tournament_scheduler.models import (
    CoachingConflict,
    ConstraintPriority,
    DivisionSpec,
    FieldSize,
    FieldSpec,
    TeamSpec,
    TimePreference,
    TimeWindow,
    TournamentSpec,
)
from tournament_scheduler.spec_io import save_spec

# Club names for realistic team generation
CLUBS = [
    "FC Thunder",
    "United SC",
    "Rapids FC",
    "Storm SC",
    "Phoenix FC",
    "Galaxy SC",
    "Strikers FC",
    "Dynamo SC",
    "Eclipse FC",
    "Blaze SC",
    "Titans FC",
    "Fusion SC",
    "Arsenal SC",
    "Celtic FC",
    "Impact SC",
    "Crossfire FC",
    "Valley SC",
    "Summit FC",
    "Pacific SC",
    "Metro FC",
    "Atlas FC",
    "Liberty SC",
    "Revolution FC",
    "Sporting SC",
    "Capital FC",
]

TEAM_SUFFIXES = ["Blue", "Red", "Gold", "White", "Black", "Green", "Elite", "Premier"]


def _make_teams(division_id: str, n_teams: int, club_pool: list[str], start_seed: int = 1) -> list[TeamSpec]:
    """Generate n_teams for a division with realistic names and clubs."""
    teams = []
    for i in range(n_teams):
        club = club_pool[i % len(club_pool)]
        suffix = TEAM_SUFFIXES[i % len(TEAM_SUFFIXES)] if i >= len(club_pool) else ""
        name = f"{club} {suffix}".strip()
        teams.append(
            TeamSpec(
                id=f"{division_id}_team_{i + 1:02d}",
                name=name,
                division_id=division_id,
                club=club,
                seed=start_seed + i,
            )
        )
    return teams


def small_tournament() -> TournamentSpec:
    """24 teams, 3 divisions, 4 fields, single Saturday.

    Divisions:
    - U10 Boys (8 teams, 7v7, medium fields)
    - U12 Girls (8 teams, 9v9, large fields)
    - U14 Boys (8 teams, 11v11, full fields)
    """
    sat = datetime(2026, 9, 12)

    divisions = [
        DivisionSpec(
            id="u10b",
            name="U10 Boys",
            field_size=FieldSize.MEDIUM,
            game_duration_minutes=25,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=45,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u12g",
            name="U12 Girls",
            field_size=FieldSize.LARGE,
            game_duration_minutes=30,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=50,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u14b",
            name="U14 Boys",
            field_size=FieldSize.FULL,
            game_duration_minutes=35,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=60,
            games_per_team=3,
            pool_size=4,
        ),
    ]

    fields = [
        FieldSpec(
            id="f1",
            name="Field 1",
            size=FieldSize.MEDIUM,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
            ],
        ),
        FieldSpec(
            id="f2",
            name="Field 2",
            size=FieldSize.MEDIUM,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
            ],
        ),
        FieldSpec(
            id="f3",
            name="Field 3",
            size=FieldSize.LARGE,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
            ],
        ),
        FieldSpec(
            id="f4",
            name="Field 4",
            size=FieldSize.FULL,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
            ],
        ),
    ]

    random.seed(42)
    clubs = random.sample(CLUBS, 12)
    teams = _make_teams("u10b", 8, clubs[:4]) + _make_teams("u12g", 8, clubs[4:8]) + _make_teams("u14b", 8, clubs[8:12])

    # Coaching conflict: one coach handles both a U10 and U12 team
    coaching_conflicts = [
        CoachingConflict(
            coach_name="Coach Martinez",
            team_ids=["u10b_team_01", "u12g_team_01"],
        ),
    ]

    return TournamentSpec(
        name="Fall Classic 2026",
        description="24-team single-day youth soccer tournament",
        divisions=divisions,
        teams=teams,
        fields=fields,
        coaching_conflicts=coaching_conflicts,
        max_solve_seconds=30,
    )


def medium_tournament() -> TournamentSpec:
    """48 teams, 5 divisions, 6 fields, Saturday + Sunday.

    Divisions:
    - U10 Boys (8 teams)
    - U10 Girls (8 teams)
    - U12 Boys (12 teams)
    - U12 Girls (8 teams)
    - U14 Boys (12 teams)
    """
    sat = datetime(2026, 10, 3)
    sun = datetime(2026, 10, 4)

    divisions = [
        DivisionSpec(
            id="u10b",
            name="U10 Boys",
            field_size=FieldSize.MEDIUM,
            game_duration_minutes=25,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=45,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u10g",
            name="U10 Girls",
            field_size=FieldSize.MEDIUM,
            game_duration_minutes=25,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=45,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u12b",
            name="U12 Boys",
            field_size=FieldSize.LARGE,
            game_duration_minutes=30,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=50,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u12g",
            name="U12 Girls",
            field_size=FieldSize.LARGE,
            game_duration_minutes=30,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=50,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u14b",
            name="U14 Boys",
            field_size=FieldSize.FULL,
            game_duration_minutes=35,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=60,
            games_per_team=3,
            pool_size=4,
        ),
    ]

    fields = [
        FieldSpec(
            id="f1",
            name="Field 1",
            size=FieldSize.MEDIUM,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=16)),
            ],
        ),
        FieldSpec(
            id="f2",
            name="Field 2",
            size=FieldSize.MEDIUM,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=16)),
            ],
        ),
        FieldSpec(
            id="f3",
            name="Field 3",
            size=FieldSize.LARGE,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=16)),
            ],
        ),
        FieldSpec(
            id="f4",
            name="Field 4",
            size=FieldSize.LARGE,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=16)),
            ],
        ),
        FieldSpec(
            id="f5",
            name="Field 5",
            size=FieldSize.FULL,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=16)),
            ],
        ),
        FieldSpec(
            id="f6",
            name="Field 6",
            size=FieldSize.FULL,
            availability=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=18)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=16)),
            ],
        ),
    ]

    random.seed(43)
    clubs = random.sample(CLUBS, 20)
    teams = (
        _make_teams("u10b", 8, clubs[:4])
        + _make_teams("u10g", 8, clubs[4:8])
        + _make_teams("u12b", 12, clubs[:6])
        + _make_teams("u12g", 8, clubs[6:10])
        + _make_teams("u14b", 12, clubs[10:16])
    )

    coaching_conflicts = [
        CoachingConflict(coach_name="Coach Davis", team_ids=["u10b_team_01", "u12b_team_01"]),
        CoachingConflict(coach_name="Coach Kim", team_ids=["u10g_team_02", "u12g_team_02"]),
    ]

    time_preferences = [
        TimePreference(
            target="u10b",
            target_type="division",
            preferred_windows=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=14)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=14)),
            ],
            priority=ConstraintPriority.MEDIUM,
        ),
        TimePreference(
            target="u10g",
            target_type="division",
            preferred_windows=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=14)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=14)),
            ],
            priority=ConstraintPriority.MEDIUM,
        ),
    ]

    return TournamentSpec(
        name="Autumn Cup 2026",
        description="48-team weekend youth soccer tournament",
        divisions=divisions,
        teams=teams,
        fields=fields,
        coaching_conflicts=coaching_conflicts,
        time_preferences=time_preferences,
        max_solve_seconds=60,
    )


def large_tournament() -> TournamentSpec:
    """96 teams, 8 divisions, 10 fields, Saturday + Sunday.

    Divisions:
    - U10 Boys (12 teams)
    - U10 Girls (12 teams)
    - U12 Boys (12 teams)
    - U12 Girls (12 teams)
    - U14 Boys (12 teams)
    - U14 Girls (12 teams)
    - U16 Boys (12 teams)
    - U16 Girls (12 teams)
    """
    sat = datetime(2026, 11, 7)
    sun = datetime(2026, 11, 8)

    divisions = [
        DivisionSpec(
            id="u10b",
            name="U10 Boys",
            field_size=FieldSize.MEDIUM,
            game_duration_minutes=25,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=45,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u10g",
            name="U10 Girls",
            field_size=FieldSize.MEDIUM,
            game_duration_minutes=25,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=45,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u12b",
            name="U12 Boys",
            field_size=FieldSize.LARGE,
            game_duration_minutes=30,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=50,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u12g",
            name="U12 Girls",
            field_size=FieldSize.LARGE,
            game_duration_minutes=30,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=50,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u14b",
            name="U14 Boys",
            field_size=FieldSize.FULL,
            game_duration_minutes=35,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=60,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u14g",
            name="U14 Girls",
            field_size=FieldSize.FULL,
            game_duration_minutes=35,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=60,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u16b",
            name="U16 Boys",
            field_size=FieldSize.FULL,
            game_duration_minutes=40,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=60,
            games_per_team=3,
            pool_size=4,
        ),
        DivisionSpec(
            id="u16g",
            name="U16 Girls",
            field_size=FieldSize.FULL,
            game_duration_minutes=40,
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=60,
            games_per_team=3,
            pool_size=4,
        ),
    ]

    fields = [
        # 3 medium fields
        FieldSpec(
            id="f1",
            name="Field 1",
            size=FieldSize.MEDIUM,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        FieldSpec(
            id="f2",
            name="Field 2",
            size=FieldSize.MEDIUM,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        FieldSpec(
            id="f3",
            name="Field 3",
            size=FieldSize.MEDIUM,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        # 3 large fields
        FieldSpec(
            id="f4",
            name="Field 4",
            size=FieldSize.LARGE,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        FieldSpec(
            id="f5",
            name="Field 5",
            size=FieldSize.LARGE,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        FieldSpec(
            id="f6",
            name="Field 6",
            size=FieldSize.LARGE,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        # 4 full fields
        FieldSpec(
            id="f7",
            name="Field 7",
            size=FieldSize.FULL,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        FieldSpec(
            id="f8",
            name="Field 8",
            size=FieldSize.FULL,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        FieldSpec(
            id="f9",
            name="Field 9",
            size=FieldSize.FULL,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
        FieldSpec(
            id="f10",
            name="Field 10",
            size=FieldSize.FULL,
            availability=[
                TimeWindow(start=sat.replace(hour=7, minute=30), end=sat.replace(hour=19)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=17)),
            ],
        ),
    ]

    random.seed(44)
    all_clubs = CLUBS * 2  # double up for large tournament
    teams: list[TeamSpec] = []
    for div in divisions:
        div_clubs = random.sample(all_clubs, 12)
        teams.extend(_make_teams(div.id, 12, div_clubs))

    coaching_conflicts = [
        CoachingConflict(coach_name="Coach Thompson", team_ids=["u10b_team_01", "u12b_team_01"]),
        CoachingConflict(coach_name="Coach Lee", team_ids=["u10g_team_03", "u14g_team_03"]),
        CoachingConflict(coach_name="Coach Patel", team_ids=["u14b_team_05", "u16b_team_05"]),
    ]

    time_preferences = [
        TimePreference(
            target="u10b",
            target_type="division",
            preferred_windows=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=14)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=14)),
            ],
            priority=ConstraintPriority.HIGH,
        ),
        TimePreference(
            target="u10g",
            target_type="division",
            preferred_windows=[
                TimeWindow(start=sat.replace(hour=8), end=sat.replace(hour=14)),
                TimeWindow(start=sun.replace(hour=8), end=sun.replace(hour=14)),
            ],
            priority=ConstraintPriority.HIGH,
        ),
    ]

    return TournamentSpec(
        name="Premier Showcase 2026",
        description="96-team weekend youth soccer showcase tournament",
        divisions=divisions,
        teams=teams,
        fields=fields,
        coaching_conflicts=coaching_conflicts,
        time_preferences=time_preferences,
        max_solve_seconds=120,
        num_workers=8,
    )


def generate_all(output_dir: str | Path = "examples") -> None:
    """Generate all fixture YAML files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    fixtures = [
        ("small_tournament.yaml", small_tournament()),
        ("medium_tournament.yaml", medium_tournament()),
        ("large_tournament.yaml", large_tournament()),
    ]

    for filename, spec in fixtures:
        path = output_dir / filename
        save_spec(spec, path)
        print(f"Generated {path} ({len(spec.teams)} teams, {len(spec.divisions)} divisions)")


if __name__ == "__main__":
    generate_all()
