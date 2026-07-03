"""Tests for spec I/O (YAML/JSON loading and saving)."""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.fixtures import small_tournament
from tournament_scheduler.spec_io import load_spec, save_spec


class TestSpecIO:
    def test_round_trip_yaml(self, tmp_path: Path):
        spec = small_tournament()
        path = tmp_path / "test.yaml"
        save_spec(spec, path)
        loaded = load_spec(path)

        assert loaded.name == spec.name
        assert len(loaded.teams) == len(spec.teams)
        assert len(loaded.divisions) == len(spec.divisions)
        assert len(loaded.fields) == len(spec.fields)

    def test_round_trip_json(self, tmp_path: Path):
        spec = small_tournament()
        path = tmp_path / "test.json"
        save_spec(spec, path)
        loaded = load_spec(path)

        assert loaded.name == spec.name
        assert len(loaded.teams) == len(spec.teams)

    def test_load_nonexistent_raises(self):
        import pytest

        with pytest.raises(FileNotFoundError):
            load_spec("/nonexistent/file.yaml")
