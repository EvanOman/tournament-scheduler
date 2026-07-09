"""Tests for the tool suite: strict schemas + dispatch behavior."""

from __future__ import annotations

from tourneydesk.session import SpecSession
from tourneydesk.tools import TOOLS, dispatch


class TestSchemas:
    # Strict compilation is off suite-wide: 17 tools with nullable unions exceed the
    # API's compiled-grammar budget (live 400s: union cap, then grammar size). Local
    # dispatch() validation + is_error retries cover what strict would guarantee.
    def test_no_tool_is_strict_and_schema_shape_holds(self):
        for tool in TOOLS:
            assert tool.get("strict") is False, f"{tool['name']} must not be strict (grammar budget)"
            schema = tool["input_schema"]
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False, f"{tool['name']} allows additionalProperties"
            props = set(schema["properties"].keys())
            required = set(schema["required"])
            assert props == required, f"{tool['name']}: required {required} != properties {props}"

    def test_every_tool_has_a_name_and_description(self):
        for tool in TOOLS:
            assert tool["name"]
            assert tool["description"]
            assert len(tool["description"]) > 10

    def test_expected_tool_names_present(self):
        names = {t["name"] for t in TOOLS}
        expected = {
            "set_tournament_info",
            "add_division",
            "update_division",
            "remove_division",
            "add_teams",
            "set_team_count",
            "add_field",
            "set_field_availability",
            "remove_field",
            "add_coaching_conflict",
            "remove_coaching_conflict",
            "add_team_avoidance",
            "remove_team_avoidance",
            "add_time_preference",
            "add_field_preference",
            "get_spec_summary",
            "mark_intake_complete",
        }
        assert expected <= names

    def test_mutation_tools_include_source_quote(self):
        for tool in TOOLS:
            if tool["name"] in ("get_spec_summary", "get_schedule_summary"):
                continue
            props = tool["input_schema"]["properties"]
            if tool["name"] == "mark_intake_complete":
                assert "confirmation_quote" in props
                continue
            assert "source_quote" in props, f"{tool['name']} missing source_quote"


class TestDispatchSuccess:
    def test_set_tournament_info_echo(self):
        session = SpecSession()
        result = dispatch(
            session,
            "set_tournament_info",
            {"name": "Fall Classic", "description": None, "source_quote": "it's the fall classic"},
        )
        assert not result.is_error
        assert "Fall Classic" in result.content
        assert session.name == "Fall Classic"

    def test_add_division_echo(self):
        session = SpecSession()
        result = dispatch(
            session,
            "add_division",
            {
                "id": "u10b",
                "name": "U10 Boys",
                "field_size": "medium",
                "game_duration_minutes": 25,
                "halftime_minutes": None,
                "buffer_minutes": None,
                "min_rest_minutes": None,
                "games_per_team": None,
                "pool_size": None,
                "bracket_after_pools": None,
                "source_quote": "u10 boys play 7v7, 25 minute games",
            },
        )
        assert not result.is_error
        assert "U10 Boys" in result.content
        assert "u10b" in session.divisions

    def test_add_teams_then_set_team_count_and_get_summary(self):
        session = SpecSession()
        dispatch(
            session,
            "add_division",
            {
                "id": "u10b",
                "name": "U10 Boys",
                "field_size": "medium",
                "game_duration_minutes": 25,
                "halftime_minutes": None,
                "buffer_minutes": None,
                "min_rest_minutes": None,
                "games_per_team": None,
                "pool_size": None,
                "bracket_after_pools": None,
                "source_quote": "q",
            },
        )
        result = dispatch(
            session,
            "add_teams",
            {
                "division_id": "u10b",
                "teams": [{"id": None, "name": "Atlas FC", "club": None, "seed": None}],
                "source_quote": "Atlas FC is in",
            },
        )
        assert not result.is_error
        assert "Atlas FC" in result.content

        summary = dispatch(session, "get_spec_summary", {})
        assert not summary.is_error
        assert "U10 Boys" in summary.content

    def test_mark_intake_complete(self):
        session = SpecSession()
        result = dispatch(session, "mark_intake_complete", {"confirmation_quote": "yep looks right"})
        assert not result.is_error
        assert session.intake_complete is True


class TestDispatchErrors:
    def test_unknown_tool_name(self):
        result = dispatch(SpecSession(), "not_a_real_tool", {})
        assert result.is_error
        assert "Unknown tool" in result.content

    def test_add_division_out_of_range_duration_is_actionable_error(self):
        session = SpecSession()
        result = dispatch(
            session,
            "add_division",
            {
                "id": "u10b",
                "name": "U10 Boys",
                "field_size": "medium",
                "game_duration_minutes": 5,  # below ge=10
                "halftime_minutes": None,
                "buffer_minutes": None,
                "min_rest_minutes": None,
                "games_per_team": None,
                "pool_size": None,
                "bracket_after_pools": None,
                "source_quote": "5 minute games",
            },
        )
        assert result.is_error
        assert result.content  # non-empty, actionable message
        assert "u10b" not in session.divisions

    def test_add_division_bad_field_size_is_actionable_error(self):
        session = SpecSession()
        result = dispatch(
            session,
            "add_division",
            {
                "id": "u10b",
                "name": "U10 Boys",
                "field_size": "enormous",  # not a valid FieldSize
                "game_duration_minutes": 25,
                "halftime_minutes": None,
                "buffer_minutes": None,
                "min_rest_minutes": None,
                "games_per_team": None,
                "pool_size": None,
                "bracket_after_pools": None,
                "source_quote": "q",
            },
        )
        assert result.is_error
        assert "u10b" not in session.divisions

    def test_add_teams_unknown_division_is_actionable_error(self):
        session = SpecSession()
        result = dispatch(
            session,
            "add_teams",
            {
                "division_id": "nope",
                "teams": [{"id": None, "name": "Atlas FC", "club": None, "seed": None}],
                "source_quote": "q",
            },
        )
        assert result.is_error
        assert "nope" in result.content

    def test_remove_division_not_found_is_error(self):
        session = SpecSession()
        result = dispatch(session, "remove_division", {"id": "nope", "source_quote": "q"})
        assert result.is_error

    def test_add_field_bad_window_is_actionable_error(self):
        session = SpecSession()
        result = dispatch(
            session,
            "add_field",
            {
                "id": "f1",
                "name": "Field 1",
                "size": "medium",
                "availability": [{"start": "2026-09-12T18:00", "end": "2026-09-12T08:00"}],  # end before start
                "source_quote": "q",
            },
        )
        assert result.is_error
        assert "f1" not in session.fields
