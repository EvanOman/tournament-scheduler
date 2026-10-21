"""Opt-in resource limits for untrusted public-demo tournament drafts.

Use ``SpecSession(limits=PUBLIC_DEMO_LIMITS)`` at the public entry point. A
normal ``SpecSession()`` remains unrestricted by this envelope. These limits
bound retained draft state and individual tool inputs; HTTP bodies, provider
history, request admission, and solver timeouts belong to their callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class SessionLimits:
    max_total_teams: int = 64
    max_teams_per_division: int = 24
    max_divisions: int = 4
    max_fields: int = 8
    max_windows_per_fact: int = 4
    max_total_windows: int = 48
    max_tournament_days: int = 2
    max_coaching_conflicts: int = 4
    max_teams_per_coach: int = 4
    max_relationships: int = 32
    max_name_chars: int = 120
    max_id_chars: int = 128
    max_game_format_chars: int = 32
    max_description_chars: int = 2000
    max_quote_chars: int = 1000
    max_input_chars: int = 16_384
    max_input_items: int = 64
    max_input_nodes: int = 512
    max_input_depth: int = 4
    max_quotes_per_fact: int = 16
    max_provenance_entries: int = 256
    max_provenance_chars: int = 32_768
    max_assumptions: int = 32
    max_game_duration_minutes: int = 90
    max_games_per_team: int = 5
    max_min_rest_minutes: int = 180
    max_buffer_minutes: int = 30

    def check(self, count: int, maximum: int, label: str) -> None:
        if count > maximum:
            raise ValueError(f"Demo limit: {label} may not exceed {maximum}.")

    def validate_input(self, values: dict[str, Any]) -> None:
        """Reject oversized input before copying, coercing, or parsing it.

        Walk the supplied objects with bounded depth/node counts rather than
        serializing another potentially large copy. Character limits include
        keys and values. Per-field text bounds also apply to nested team data.
        """
        nodes = 0
        characters = 0
        text_limits = {
            "name": self.max_name_chars,
            "coach_name": self.max_name_chars,
            "club": self.max_name_chars,
            "id": self.max_id_chars,
            "division_id": self.max_id_chars,
            "field_id": self.max_id_chars,
            "target": self.max_id_chars,
            "team_ids": self.max_id_chars,
            "field_ids": self.max_id_chars,
            "game_format": self.max_game_format_chars,
            "description": self.max_description_chars,
            "reason": self.max_description_chars,
            "source_quote": self.max_quote_chars,
            "confirmation_quote": self.max_quote_chars,
        }

        def visit(value: Any, depth: int, key: str = "") -> None:
            nonlocal nodes, characters
            nodes += 1
            self.check(nodes, self.max_input_nodes, "tool input nodes")
            self.check(depth, self.max_input_depth, "tool input nesting")
            if value is None:
                if key in {"source_quote", "confirmation_quote"}:
                    raise ValueError(f"Demo input: {key} must be text.")
                return
            if isinstance(value, str):
                self.check(len(value), text_limits.get(key, self.max_description_chars), key or "text length")
                characters += len(value)
                self.check(characters, self.max_input_chars, "tool input characters")
            elif isinstance(value, (list, tuple)):
                self.check(len(value), self.max_input_items, "tool input collection items")
                if key in text_limits and key not in {"team_ids", "field_ids"}:
                    raise ValueError(f"Demo input: {key} must be text.")
                for item in value:
                    visit(item, depth + 1, key)
            elif isinstance(value, dict):
                self.check(len(value), self.max_input_items, "tool input collection items")
                if key in text_limits:
                    raise ValueError(f"Demo input: {key} must be text.")
                for child_key, child in value.items():
                    if not isinstance(child_key, str):
                        raise ValueError("Demo input: object keys must be text.")
                    self.check(len(child_key), self.max_id_chars, "tool input key length")
                    characters += len(child_key)
                    self.check(characters, self.max_input_chars, "tool input characters")
                    visit(child, depth + 1, child_key)
            elif isinstance(value, datetime):
                if key not in {"start", "end"}:
                    raise ValueError("Demo input: datetimes are only valid in time windows.")
            elif isinstance(value, (bool, int, float)):
                if key in text_limits:
                    raise ValueError(f"Demo input: {key} must be text.")
                if not isinstance(value, int) and not isfinite(value):
                    raise ValueError("Demo input: numbers must be finite.")
                if abs(value) > 1_000_000:
                    raise ValueError("Demo limit: tool input numbers may not exceed 1000000.")
            else:
                raise ValueError("Demo input: use JSON values or time-window datetimes.")

        if not isinstance(values, dict):
            raise ValueError("Demo input: tool arguments must be an object.")
        visit(values, 0)


PUBLIC_DEMO_LIMITS = SessionLimits()
