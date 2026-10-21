"""Public demo limits apply at both the session and model-tool seams."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from tourneydesk.limits import PUBLIC_DEMO_LIMITS
from tourneydesk.session import SpecSession
from tourneydesk.tools import dispatch


def _window(day: int = 12) -> list[dict[str, str]]:
    return [{"start": f"2027-06-{day:02d}T08:00", "end": f"2027-06-{day:02d}T18:00"}]


def _division(session: SpecSession, id: str = "d1") -> None:
    session.add_division(id=id, name=id, field_size="full", game_duration_minutes=30, source_quote="q")


def _session(**limit_changes: int) -> SpecSession:
    session = SpecSession(limits=replace(PUBLIC_DEMO_LIMITS, **limit_changes))
    _division(session)
    session.add_teams(division_id="d1", teams=[{"name": "One"}, {"name": "Two"}], source_quote="q")
    session.add_field(id="f1", name="Field 1", size="full", availability=_window(), source_quote="q")
    return session


def _assert_dispatch_rejected(session: SpecSession, name: str, arguments: dict, message: str = "Demo") -> None:
    before = deepcopy(session.__dict__)
    result = dispatch(session, name, arguments)
    assert result.is_error, result.content
    assert message in result.content
    assert session.__dict__ == before


def test_default_sessions_remain_unrestricted() -> None:
    session = SpecSession()
    for n in range(5):
        _division(session, f"d{n}")
    session.set_team_count(division_id="d0", count=65, source_quote="q" * 1001)
    for n in range(9):
        session.add_field(
            id=f"f{n}",
            name="Field" * 30,
            size="full",
            availability=[{"start": "2027-06-12T08:00", "end": "2027-06-15T18:00"}],
            source_quote="q",
        )
    assert len(session.teams) == 65
    assert len(session.fields) == 9


def test_team_count_accepts_exact_public_caps_and_rejects_total_before_replacing() -> None:
    session = SpecSession(limits=PUBLIC_DEMO_LIMITS)
    for id, count in [("d1", 24), ("d2", 24), ("d3", 16)]:
        _division(session, id)
        session.set_team_count(division_id=id, count=count, source_quote="q")
    assert len(session.teams) == 64
    original_team = session.teams["d3_team_01"]
    _assert_dispatch_rejected(session, "set_team_count", {"division_id": "d3", "count": 17, "source_quote": "q"})
    assert session.teams["d3_team_01"] is original_team
    _assert_dispatch_rejected(session, "set_team_count", {"division_id": "d3", "count": 25, "source_quote": "q"})


def test_named_team_batches_cannot_bypass_per_division_or_total_caps() -> None:
    session = SpecSession(limits=PUBLIC_DEMO_LIMITS)
    for id, count in [("d1", 24), ("d2", 24), ("d3", 15)]:
        _division(session, id)
        session.set_team_count(division_id=id, count=count, source_quote="q")
    session.add_teams(division_id="d3", teams=[{"id": "named", "name": "Named"}], source_quote="q")
    assert len(session.teams) == 64
    _assert_dispatch_rejected(
        session, "add_teams", {"division_id": "d3", "teams": [{"name": "Another"}], "source_quote": "q"}
    )
    _assert_dispatch_rejected(
        session, "add_teams", {"division_id": "d1", "teams": [{"name": "Another"}], "source_quote": "q"}
    )


@pytest.mark.parametrize(
    "teams",
    [
        [{"name": "New"}, {"name": "One"}],
        [{"name": "New"}, {"name": "Bad seed", "seed": "not a seed"}],
        [{"name": "New"}, {}],
        [{"name": "New", "id": "repeated"}, {"name": "Other", "id": "repeated"}],
        [{"name": "New", "id": "d1_one"}],
    ],
)
def test_rejected_team_batch_never_leaves_an_earlier_entry_or_overwrites_a_team(teams: list[dict]) -> None:
    session = _session()
    _assert_dispatch_rejected(session, "add_teams", {"division_id": "d1", "teams": teams, "source_quote": "q"}, "")


def test_auto_derived_ids_remain_unique_inside_a_valid_batch() -> None:
    session = _session()
    teams = session.add_teams(division_id="d1", teams=[{"name": "A-B"}, {"name": "A B"}], source_quote="q")
    assert [team.id for team in teams] == ["d1_a_b", "d1_a_b_2"]


def test_division_and_field_limits_count_replacements_without_losing_atomicity() -> None:
    session = _session()
    for n in range(2, 5):
        _division(session, f"d{n}")
    _division(session, "d4")
    _assert_dispatch_rejected(
        session,
        "add_division",
        {"id": "d5", "name": "Fifth", "field_size": "full", "game_duration_minutes": 30, "source_quote": "q"},
    )
    for n in range(2, 9):
        session.add_field(id=f"f{n}", name=f"Field {n}", size="full", availability=_window(), source_quote="q")
    session.add_field(id="f8", name="Replacement", size="full", availability=_window(13), source_quote="q")
    _assert_dispatch_rejected(
        session,
        "add_field",
        {"id": "f9", "name": "Ninth", "size": "full", "availability": _window(), "source_quote": "q"},
    )


def test_excessive_window_count_is_rejected_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()

    def unexpected_parse(*args, **kwargs):
        pytest.fail("oversized window arrays must be rejected before model allocation")

    monkeypatch.setattr("tourneydesk.session.TimeWindow.model_validate", unexpected_parse)
    _assert_dispatch_rejected(
        session,
        "set_field_availability",
        {"field_id": "f1", "availability": _window() * 5, "source_quote": "q"},
    )


def test_overlapping_windows_cannot_multiply_available_slot_minutes() -> None:
    session = _session()
    two_days = [{"start": "2027-06-12T00:00", "end": "2027-06-14T00:00"}]
    _assert_dispatch_rejected(
        session, "set_field_availability", {"field_id": "f1", "availability": two_days * 2, "source_quote": "q"}
    )


@pytest.mark.parametrize("count", [2.5, "3", True])
def test_invalid_count_types_do_not_delete_the_existing_roster(count: object) -> None:
    _assert_dispatch_rejected(_session(), "set_team_count", {"division_id": "d1", "count": count, "source_quote": "q"})


def test_generated_placeholder_ids_cannot_overwrite_another_division() -> None:
    session = _session()
    _division(session, "d2")
    session.add_teams(division_id="d2", teams=[{"id": "d1_team_01", "name": "Other division"}], source_quote="q")
    _assert_dispatch_rejected(session, "set_team_count", {"division_id": "d1", "count": 2, "source_quote": "q"}, "")


@pytest.mark.parametrize(
    "updates", [{"game_duration_minutes": 91}, {"games_per_team": 6}, {"min_rest_minutes": 181}, {"buffer_minutes": 31}]
)
def test_public_division_values_share_the_demo_solve_envelope(updates: dict) -> None:
    _assert_dispatch_rejected(_session(), "update_division", {"id": "d1", **updates, "source_quote": "q"})


def test_mixed_aware_and_naive_windows_are_rejected_without_a_partial_field_update() -> None:
    session = _session()
    _assert_dispatch_rejected(
        session,
        "add_field",
        {
            "id": "f2",
            "name": "Second",
            "size": "full",
            "availability": [{"start": "2027-06-12T08:00Z", "end": "2027-06-12T18:00Z"}],
            "source_quote": "q",
        },
    )


@pytest.mark.parametrize(
    "name, arguments",
    [
        (
            "set_field_availability",
            {"field_id": "f1", "availability": [{"start": "2027-06-12T08:00", "end": "2027-06-14T18:00"}]},
        ),
        ("add_field", {"id": "f2", "name": "Second", "size": "full", "availability": _window(14)}),
        ("add_time_preference", {"target": "d1", "target_type": "division", "windows": _window(14)}),
    ],
)
def test_all_window_paths_enforce_the_two_day_horizon(name: str, arguments: dict) -> None:
    session = _session()
    session.add_field(id="f2", name="Second", size="full", availability=_window(13), source_quote="q")
    _assert_dispatch_rejected(session, name, {**arguments, "source_quote": "q"})


def test_window_replacement_credits_existing_windows_and_permits_moving_the_whole_event() -> None:
    session = _session(max_total_windows=2)
    session.set_field_availability(field_id="f1", availability=_window(13) + _window(14), source_quote="q")
    assert len(session.fields["f1"].availability) == 2
    _assert_dispatch_rejected(
        session,
        "add_time_preference",
        {"target": "d1", "target_type": "division", "windows": _window(13), "source_quote": "q"},
    )


def test_relationship_budget_counts_every_kind_and_credits_replacement() -> None:
    session = _session(max_relationships=2)
    session.add_coaching_conflict(coach_name="Lee", team_ids=["d1_one", "d1_two"], source_quote="q")
    session.add_team_avoidance(team_ids=["d1_one", "d1_two"], reason=None, source_quote="q")
    session.add_coaching_conflict(coach_name="Lee", team_ids=["d1_one", "d1_two"], source_quote="changed")
    session.add_team_avoidance(team_ids=["d1_two", "d1_one"], reason="updated", source_quote="changed")
    for name, arguments in [
        ("add_coaching_conflict", {"coach_name": "Other", "team_ids": ["d1_one", "d1_two"]}),
        ("add_time_preference", {"target": "d1", "target_type": "division", "windows": _window()}),
        ("add_field_preference", {"target": "d1", "target_type": "division", "field_ids": ["f1"]}),
    ]:
        _assert_dispatch_rejected(session, name, {**arguments, "source_quote": "q"})


def test_coach_conflicts_and_relationship_members_are_bounded() -> None:
    session = _session()
    for n in range(4):
        session.add_coaching_conflict(coach_name=f"Coach {n}", team_ids=["d1_one", "d1_two"], source_quote="q")
    _assert_dispatch_rejected(
        session, "add_coaching_conflict", {"coach_name": "Fifth", "team_ids": ["d1_one", "d1_two"], "source_quote": "q"}
    )
    _assert_dispatch_rejected(
        session, "add_coaching_conflict", {"coach_name": "Coach 0", "team_ids": ["d1_one"] * 5, "source_quote": "q"}
    )
    _assert_dispatch_rejected(
        session,
        "add_field_preference",
        {"target": "d1", "target_type": "division", "field_ids": ["f1"] * 9, "source_quote": "q"},
    )


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("set_tournament_info", {"name": "N" * 121, "description": None}),
        ("set_tournament_info", {"name": "Name", "description": "D" * 2001}),
        ("update_division", {"id": "d1", "game_format": "F" * 33}),
        ("add_teams", {"division_id": "d1", "teams": [{"id": "I" * 129, "name": "New"}]}),
        ("add_teams", {"division_id": "d1", "teams": [{"name": "New", "club": "C" * 121}]}),
        ("add_team_avoidance", {"team_ids": ["d1_one", "d1_two"], "reason": "R" * 2001}),
    ],
)
def test_stored_text_is_bounded_before_mutation(name: str, arguments: dict) -> None:
    _assert_dispatch_rejected(_session(), name, {**arguments, "source_quote": "q"})


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("set_tournament_info", {"name": "Name", "description": None}),
        ("add_division", {"id": "d2", "name": "Second", "field_size": "full", "game_duration_minutes": 30}),
        ("update_division", {"id": "d1", "name": "Changed"}),
        ("remove_division", {"id": "d1"}),
        ("add_teams", {"division_id": "d1", "teams": [{"name": "New"}]}),
        ("set_team_count", {"division_id": "d1", "count": 3}),
        ("add_field", {"id": "f2", "name": "Second", "size": "full", "availability": _window()}),
        ("set_field_availability", {"field_id": "f1", "availability": _window(13)}),
        ("remove_field", {"id": "f1"}),
        ("add_coaching_conflict", {"coach_name": "Lee", "team_ids": ["d1_one", "d1_two"]}),
        ("remove_coaching_conflict", {"coach_name": "Lee"}),
        ("add_team_avoidance", {"team_ids": ["d1_one", "d1_two"], "reason": None}),
        ("remove_team_avoidance", {"team_ids": ["d1_one", "d1_two"]}),
        ("add_time_preference", {"target": "d1", "target_type": "division", "windows": _window()}),
        ("add_field_preference", {"target": "d1", "target_type": "division", "field_ids": ["f1"]}),
        ("remove_time_preference", {"target": "d1"}),
        ("remove_field_preference", {"target": "d1"}),
    ],
)
def test_every_mutation_rejects_oversized_provenance(name: str, arguments: dict) -> None:
    _assert_dispatch_rejected(_session(), name, {**arguments, "source_quote": "q" * 1001})


def test_confirmation_and_ignored_tool_input_are_also_bounded() -> None:
    session = _session()
    _assert_dispatch_rejected(session, "mark_intake_complete", {"confirmation_quote": "q" * 1001})
    _assert_dispatch_rejected(session, "get_spec_summary", {"ignored": ["q" * 1000] * 17})
    _assert_dispatch_rejected(session, "get_spec_summary", {"ignored": [0] * 65})
    _assert_dispatch_rejected(session, "get_spec_summary", {"ignored": [[[[["too deep"]]]]]})


def test_direct_session_calls_cannot_bypass_the_input_envelope() -> None:
    session = _session()
    before = deepcopy(session.__dict__)
    with pytest.raises(ValueError, match="Demo"):
        session.set_tournament_info(name="Name", description=None, source_quote="q" * 1001)
    assert session.__dict__ == before


def test_quote_history_is_bounded_per_fact_without_changing_the_fact() -> None:
    session = _session(max_quotes_per_fact=2)
    session.update_division(id="d1", name="Second", source_quote="q2")
    original = session.divisions["d1"]
    _assert_dispatch_rejected(session, "update_division", {"id": "d1", "name": "Third", "source_quote": "q3"})
    assert session.divisions["d1"] is original


def test_global_provenance_entry_budget_accounts_for_every_created_team() -> None:
    session = SpecSession(limits=replace(PUBLIC_DEMO_LIMITS, max_provenance_entries=4))
    _division(session)
    session.set_team_count(division_id="d1", count=2, source_quote="q")
    _assert_dispatch_rejected(session, "set_tournament_info", {"name": "Name", "source_quote": "q"})
    _assert_dispatch_rejected(session, "set_team_count", {"division_id": "d1", "count": 3, "source_quote": "q"})


def test_global_provenance_character_budget_and_completion_replacement() -> None:
    session = SpecSession(limits=replace(PUBLIC_DEMO_LIMITS, max_provenance_chars=8))
    session.set_tournament_info(name="Name", description=None, source_quote="1234")
    session.mark_intake_complete(confirmation_quote="5678")
    session.mark_intake_complete(confirmation_quote="abcd")
    _assert_dispatch_rejected(session, "mark_intake_complete", {"confirmation_quote": "abcde"})
    _assert_dispatch_rejected(session, "set_tournament_info", {"name": "Changed", "source_quote": "9"})


def test_repeated_placeholder_replacements_cannot_grow_assumptions_forever() -> None:
    session = _session(max_assumptions=2)
    session.set_team_count(division_id="d1", count=2, source_quote="q1")
    session.set_team_count(division_id="d1", count=2, source_quote="q2")
    _assert_dispatch_rejected(session, "set_team_count", {"division_id": "d1", "count": 2, "source_quote": "q3"})
