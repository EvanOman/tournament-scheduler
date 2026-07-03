"""Load and save tournament specifications from YAML/JSON files."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tournament_scheduler.models import TournamentSpec


def load_spec(path: str | Path) -> TournamentSpec:
    """Load a tournament spec from a YAML or JSON file."""
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Spec file not found: {path}")

    text = path.read_text()

    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    elif path.suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"Unsupported file format: {path.suffix} (expected .yaml, .yml, or .json)")

    return TournamentSpec.model_validate(data)


def save_spec(spec: TournamentSpec, path: str | Path) -> None:
    """Save a tournament spec to a YAML file."""
    path = Path(path)
    data = spec.model_dump(mode="json")

    if path.suffix in (".yaml", ".yml"):
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    elif path.suffix == ".json":
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    else:
        raise ValueError(f"Unsupported file format: {path.suffix}")
