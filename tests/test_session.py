"""Tests for SpecSession: mutations, provenance, to_spec() assumptions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tourneydesk.session import IncompleteSpecError, SpecSession


def _availability() -> list[dict[str, str]]:
    return [{"start": "2026-09-12T08:00", "end": "2026-09-12T18:00"}]


class TestBasicMutations:
    def test_set_tournament_info_records_name_and_quote(self):
        session = SpecSession()
        session.set_tournament_info(name="Fall Classic", description=None, source_quote="It's the Fall Classic")
        assert session.name == "Fall Classic"
        assert session._name_quotes == ["It's the Fall Classic"]

    def test_add_division_stores_required_and_optional_fields(self):
        session = SpecSession()
        d = session.add_division(
            id="u10b",
            name="U10 Boys",
            field_size="medium",
            game_duration_minutes=25,
            games_per_team=3,
            source_quote="U10 boys play 7v7, 25 minute games, 3 games each",
        )
        assert d.id == "u10b"
        assert d.games_per_team == 3
        assert d.halftime_minutes is None  # not stated
        assert d.source_quotes == ["U10 boys play 7v7, 25 minute games, 3 games each"]

    def test_add_division_rejects_out_of_range_duration(self):
        session = SpecSession()
        with pytest.raises(ValidationError):
            session.add_division(
                id="u10b",
                name="U10 Boys",
                field_size="medium",
                game_duration_minutes=1,  # below DivisionSpec's ge=10
                source_quote="1 minute games",
            )

    def test_update_division_only_changes_given_fields(self):
        session = SpecSession()
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q1"
        )
        session.update_division(id="u10b", games_per_team=4, source_quote="actually 4 games each")
        d = session.divisions["u10b"]
        assert d.games_per_team == 4
        assert d.name == "U10 Boys"  # unchanged
        assert d.source_quotes == ["q1", "actually 4 games each"]

    def test_remove_division_cascades_to_teams(self):
        session = SpecSession()
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q"
        )
        session.add_teams(
            division_id="u10b", teams=[{"id": None, "name": "Atlas FC", "club": None, "seed": None}], source_quote="q"
        )
        assert session.remove_division(id="u10b", source_quote="cancel u10b") is True
        assert "u10b" not in session.divisions
        assert not session.teams

    def test_add_teams_auto_derives_id_from_name(self):
        session = SpecSession()
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q"
        )
        created = session.add_teams(
            division_id="u10b",
            teams=[{"id": None, "name": "Atlas FC", "club": None, "seed": None}],
            source_quote="Atlas FC is playing",
        )
        assert created[0].id == "u10b_atlas_fc"
        assert created[0].source_quotes == ["Atlas FC is playing"]

    def test_add_teams_unknown_division_raises(self):
        session = SpecSession()
        with pytest.raises(ValueError, match="Unknown division"):
            session.add_teams(
                division_id="nope", teams=[{"id": None, "name": "X", "club": None, "seed": None}], source_quote="q"
            )

    def test_set_team_count_creates_placeholders_and_records_assumption(self):
        session = SpecSession()
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q"
        )
        created = session.set_team_count(division_id="u10b", count=6, source_quote="12 teams -- wait, 6 teams")
        assert len(created) == 6
        assert any("placeholder team names for U10 Boys" in a for a in session._assumptions)

    def test_set_team_count_replaces_previous_teams_in_division(self):
        session = SpecSession()
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q"
        )
        session.set_team_count(division_id="u10b", count=4, source_quote="q")
        session.set_team_count(division_id="u10b", count=6, source_quote="q2")
        assert len(session.teams) == 6

    def test_add_field_and_set_field_availability(self):
        session = SpecSession()
        f = session.add_field(id="f1", name="Field 1", size="medium", availability=_availability(), source_quote="q")
        assert len(f.availability) == 1
        session.set_field_availability(
            field_id="f1",
            availability=[{"start": "2026-09-12T09:00", "end": "2026-09-12T17:00"}],
            source_quote="actually 9 to 5",
        )
        assert len(session.fields["f1"].availability) == 1
        assert session.fields["f1"].source_quotes == ["q", "actually 9 to 5"]

    def test_set_field_availability_unknown_field_raises(self):
        session = SpecSession()
        with pytest.raises(ValueError, match="Unknown field"):
            session.set_field_availability(field_id="nope", availability=_availability(), source_quote="q")

    def test_remove_field(self):
        session = SpecSession()
        session.add_field(id="f1", name="Field 1", size="medium", availability=_availability(), source_quote="q")
        assert session.remove_field(id="f1", source_quote="q") is True
        assert session.remove_field(id="f1", source_quote="q") is False

    def test_coaching_conflict_add_and_remove(self):
        session = SpecSession()
        session.add_coaching_conflict(coach_name="Coach Lee", team_ids=["t1", "t2"], source_quote="q")
        assert len(session.coaching_conflicts) == 1
        assert session.remove_coaching_conflict(coach_name="Coach Lee", source_quote="q") is True
        assert not session.coaching_conflicts

    def test_add_coaching_conflict_replaces_same_coach(self):
        session = SpecSession()
        session.add_coaching_conflict(coach_name="Coach Lee", team_ids=["t1", "t2"], source_quote="q")
        session.add_coaching_conflict(coach_name="Coach Lee", team_ids=["t1", "t2", "t3"], source_quote="q2")
        assert len(session.coaching_conflicts) == 1
        assert session.coaching_conflicts[0][0].team_ids == ["t1", "t2", "t3"]

    def test_team_avoidance_add_and_remove(self):
        session = SpecSession()
        session.add_team_avoidance(team_ids=["t1", "t2"], reason="siblings", source_quote="q")
        assert len(session.team_avoidances) == 1
        assert session.remove_team_avoidance(team_ids=["t2", "t1"], source_quote="q") is True
        assert not session.team_avoidances

    def test_time_preference_defaults_priority_medium(self):
        session = SpecSession()
        pref = session.add_time_preference(
            target="u10b",
            target_type="division",
            windows=_availability(),
            priority=None,
            source_quote="prefer mornings",
        )
        assert pref.priority.value == "medium"

    def test_field_preference_defaults_priority_low(self):
        session = SpecSession()
        pref = session.add_field_preference(
            target="u10b", target_type="division", field_ids=["f1"], priority=None, source_quote="prefer field 1"
        )
        assert pref.priority.value == "low"

    def test_mark_intake_complete(self):
        session = SpecSession()
        assert session.intake_complete is False
        session.mark_intake_complete(confirmation_quote="yep that's right")
        assert session.intake_complete is True


class TestToSpec:
    def _complete_session(self) -> SpecSession:
        session = SpecSession()
        session.set_tournament_info(name="Fall Classic", description=None, source_quote="q")
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q"
        )
        session.add_teams(
            division_id="u10b",
            teams=[
                {"id": None, "name": "Atlas FC", "club": None, "seed": None},
                {"id": None, "name": "Storm SC", "club": None, "seed": None},
            ],
            source_quote="q",
        )
        session.add_field(id="f1", name="Field 1", size="medium", availability=_availability(), source_quote="q")
        return session

    def test_to_spec_raises_when_no_divisions(self):
        session = SpecSession()
        with pytest.raises(IncompleteSpecError) as excinfo:
            session.to_spec()
        assert any("division" in m.lower() for m in excinfo.value.missing)

    def test_to_spec_raises_when_fewer_than_two_teams(self):
        session = SpecSession()
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q"
        )
        session.add_field(id="f1", name="Field 1", size="medium", availability=_availability(), source_quote="q")
        with pytest.raises(IncompleteSpecError) as excinfo:
            session.to_spec()
        assert any("two teams" in m.lower() for m in excinfo.value.missing)

    def test_to_spec_raises_when_no_fields(self):
        session = SpecSession()
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q"
        )
        session.add_teams(
            division_id="u10b",
            teams=[
                {"id": None, "name": "Atlas FC", "club": None, "seed": None},
                {"id": None, "name": "Storm SC", "club": None, "seed": None},
            ],
            source_quote="q",
        )
        with pytest.raises(IncompleteSpecError) as excinfo:
            session.to_spec()
        assert any("field" in m.lower() for m in excinfo.value.missing)

    def test_to_spec_raises_when_field_has_no_availability(self):
        session = self._complete_session()
        session.fields["f1"].availability = []
        with pytest.raises(IncompleteSpecError) as excinfo:
            session.to_spec()
        assert any("availability" in m.lower() for m in excinfo.value.missing)

    def test_to_spec_never_fabricates_availability(self):
        # A field with no stated availability is a hard block, not an assumption.
        session = self._complete_session()
        session.fields["f1"].availability = []
        with pytest.raises(IncompleteSpecError):
            session.to_spec()

    def test_to_spec_returns_valid_spec_with_no_assumptions_when_fully_stated(self):
        session = self._complete_session()
        session.update_division(
            id="u10b",
            halftime_minutes=5,
            buffer_minutes=10,
            min_rest_minutes=45,
            games_per_team=3,
            pool_size=4,
            bracket_after_pools=True,
            source_quote="all the details",
        )
        spec, assumptions = session.to_spec()
        assert spec.name == "Fall Classic"
        assert len(spec.teams) == 2
        assert assumptions == []

    def test_to_spec_labels_defaulted_optional_fields_as_assumptions(self):
        session = self._complete_session()
        spec, assumptions = session.to_spec()
        assert spec.divisions[0].games_per_team == 3  # pydantic default applied
        assert any("games_per_team" not in a and "3 pool-play games" in a for a in assumptions)
        assert any("U10 Boys" in a for a in assumptions)

    def test_to_spec_labels_missing_name_as_assumption(self):
        session = SpecSession()
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="q"
        )
        session.add_teams(
            division_id="u10b",
            teams=[
                {"id": None, "name": "Atlas FC", "club": None, "seed": None},
                {"id": None, "name": "Storm SC", "club": None, "seed": None},
            ],
            source_quote="q",
        )
        session.add_field(id="f1", name="Field 1", size="medium", availability=_availability(), source_quote="q")
        spec, assumptions = session.to_spec()
        assert spec.name == "Untitled Tournament"
        assert any("Untitled Tournament" in a for a in assumptions)


class TestRulesJson:
    def test_to_rules_json_groups_by_category_with_quotes(self):
        session = SpecSession()
        session.set_tournament_info(name="Fall Classic", description=None, source_quote="it's the fall classic")
        session.add_division(
            id="u10b", name="U10 Boys", field_size="medium", game_duration_minutes=25, source_quote="u10 boys, 7v7"
        )
        rules = session.to_rules_json()
        assert rules["tournament"]["name"] == "Fall Classic"
        assert rules["tournament"]["source_quotes"] == ["it's the fall classic"]
        assert rules["divisions"][0]["source_quotes"] == ["u10 boys, 7v7"]
        assert rules["intake_complete"] is False
        for category in (
            "teams",
            "fields",
            "coaching_conflicts",
            "team_avoidances",
            "time_preferences",
            "field_preferences",
        ):
            assert category in rules
