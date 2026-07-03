"""Tests for tournament spec models and validation."""

from __future__ import annotations

from datetime import datetime

import pytest

from tournament_scheduler.models import (
    DivisionSpec,
    FieldSize,
    TimeWindow,
    TournamentSpec,
)


def _minimal_spec(**overrides) -> dict:
    """Build a minimal valid spec dict for testing."""
    base = {
        "name": "Test Tournament",
        "divisions": [
            {
                "id": "d1",
                "name": "U12 Boys",
                "field_size": "large",
                "game_duration_minutes": 30,
            }
        ],
        "teams": [
            {"id": "t1", "name": "Team 1", "division_id": "d1"},
            {"id": "t2", "name": "Team 2", "division_id": "d1"},
        ],
        "fields": [
            {
                "id": "f1",
                "name": "Field 1",
                "size": "large",
                "availability": [{"start": "2026-09-12T08:00:00", "end": "2026-09-12T18:00:00"}],
            }
        ],
    }
    base.update(overrides)
    return base


class TestTournamentSpec:
    def test_minimal_spec_valid(self):
        spec = TournamentSpec.model_validate(_minimal_spec())
        assert spec.name == "Test Tournament"
        assert len(spec.teams) == 2
        assert len(spec.divisions) == 1
        assert len(spec.fields) == 1

    def test_duplicate_team_ids_rejected(self):
        data = _minimal_spec()
        data["teams"].append({"id": "t1", "name": "Duplicate", "division_id": "d1"})
        with pytest.raises(ValueError, match="unique"):
            TournamentSpec.model_validate(data)

    def test_duplicate_field_ids_rejected(self):
        data = _minimal_spec()
        data["fields"].append(
            {
                "id": "f1",
                "name": "Dup Field",
                "size": "large",
                "availability": [{"start": "2026-09-12T08:00:00", "end": "2026-09-12T18:00:00"}],
            }
        )
        with pytest.raises(ValueError, match="unique"):
            TournamentSpec.model_validate(data)

    def test_unknown_division_rejected(self):
        data = _minimal_spec()
        data["teams"].append({"id": "t3", "name": "Team 3", "division_id": "nonexistent"})
        with pytest.raises(ValueError, match="unknown division"):
            TournamentSpec.model_validate(data)

    def test_teams_in_division(self):
        spec = TournamentSpec.model_validate(_minimal_spec())
        assert len(spec.teams_in_division("d1")) == 2
        assert len(spec.teams_in_division("nonexistent")) == 0

    def test_fields_for_size(self):
        spec = TournamentSpec.model_validate(_minimal_spec())
        assert len(spec.fields_for_size(FieldSize.LARGE)) == 1
        assert len(spec.fields_for_size(FieldSize.SMALL)) == 0

    def test_total_game_minutes(self):
        spec = TournamentSpec.model_validate(_minimal_spec())
        division = spec.divisions[0]
        # 30 (game) + 0 (halftime default) + 10 (buffer default) = 40
        assert spec.total_game_minutes(division) == 40


class TestTimeWindow:
    def test_valid_window(self):
        w = TimeWindow(start=datetime(2026, 9, 12, 8), end=datetime(2026, 9, 12, 18))
        assert w.start < w.end

    def test_invalid_window_rejected(self):
        with pytest.raises(ValueError, match="end must be after start"):
            TimeWindow(start=datetime(2026, 9, 12, 18), end=datetime(2026, 9, 12, 8))


class TestDivisionSpec:
    def test_defaults(self):
        d = DivisionSpec(id="d1", name="U12", field_size=FieldSize.LARGE, game_duration_minutes=30)
        assert d.buffer_minutes == 10
        assert d.min_rest_minutes == 60
        assert d.games_per_team == 3
        assert d.pool_size == 4
