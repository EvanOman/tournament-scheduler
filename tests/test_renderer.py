"""Tests for schedule rendering."""

from __future__ import annotations

from tournament_scheduler.fixtures import small_tournament
from tournament_scheduler.pools import assign_pools
from tournament_scheduler.renderer import render_html, render_markdown
from tournament_scheduler.solver import solve


class TestMarkdownRenderer:
    def test_renders_markdown(self):
        spec = small_tournament()
        pools = assign_pools(spec)
        schedule = solve(spec, pools)
        md = render_markdown(schedule, spec)

        assert "# Fall Classic 2026" in md
        assert "## Pools" in md
        assert "## Field Schedule" in md
        assert "## Team Itineraries" in md
        assert "| Time |" in md

    def test_contains_all_teams(self):
        spec = small_tournament()
        pools = assign_pools(spec)
        schedule = solve(spec, pools)
        md = render_markdown(schedule, spec)

        for team in spec.teams:
            assert team.name in md, f"Team {team.name} not found in markdown output"


class TestHtmlRenderer:
    def test_renders_html(self):
        spec = small_tournament()
        pools = assign_pools(spec)
        schedule = solve(spec, pools)
        html = render_html(schedule, spec)

        assert "<title>" in html
        assert "Fall Classic 2026" in html
        assert "<table>" in html

    def test_contains_stats(self):
        spec = small_tournament()
        pools = assign_pools(spec)
        schedule = solve(spec, pools)
        html = render_html(schedule, spec)

        assert "Teams:" in html
        assert "Fields:" in html
        assert "Games:" in html
