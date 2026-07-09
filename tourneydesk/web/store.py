"""Boring SQLite persistence for sessions: one row, spec + transcript as JSON.

Deliberately minimal (goal-prompt: "keep it boring"). A session's *live*
conversation object lives in memory in the SessionManager; this store keeps the
durable artifacts -- title, the Rules-panel JSON, and the chat transcript -- so
the session list survives a reload and `GET /api/sessions/{id}` can render a
session that isn't currently connected. Rehydrating a fully live provider across
a process restart is out of scope for the M3 slice (see DECISIONS.md).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    rules_json  TEXT NOT NULL DEFAULT '{}',
    transcript  TEXT NOT NULL DEFAULT '[]'
);
"""


@dataclass
class SessionRow:
    id: str
    title: str
    created_at: float
    updated_at: float
    rules: dict[str, Any]
    transcript: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        divisions = self.rules.get("divisions", []) if isinstance(self.rules, dict) else []
        teams = self.rules.get("teams", []) if isinstance(self.rules, dict) else []
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "num_divisions": len(divisions),
            "num_teams": len(teams),
            "intake_complete": bool(self.rules.get("intake_complete")) if isinstance(self.rules, dict) else False,
        }


class SessionStore:
    """Thin SQLite wrapper. `:memory:` is honoured for tests via a shared connection."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._path = str(db_path)
        # check_same_thread=False: solves run in worker threads; access is
        # serialised by the manager per session so this stays safe and boring.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def create(self, title: str) -> SessionRow:
        now = time.time()
        sid = uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sid, title, now, now),
        )
        self._conn.commit()
        return SessionRow(id=sid, title=title, created_at=now, updated_at=now, rules={}, transcript=[])

    def get(self, sid: str) -> SessionRow | None:
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return _row_to_session(row) if row else None

    def list_all(self) -> list[SessionRow]:
        rows = self._conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return [_row_to_session(r) for r in rows]

    def save_state(
        self,
        sid: str,
        *,
        rules: dict[str, Any] | None = None,
        transcript: list[dict[str, Any]] | None = None,
        title: str | None = None,
    ) -> None:
        sets = ["updated_at = ?"]
        args: list[Any] = [time.time()]
        if rules is not None:
            sets.append("rules_json = ?")
            args.append(json.dumps(rules, default=str))
        if transcript is not None:
            sets.append("transcript = ?")
            args.append(json.dumps(transcript, default=str))
        if title is not None:
            sets.append("title = ?")
            args.append(title)
        args.append(sid)
        self._conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", args)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        rules=json.loads(row["rules_json"] or "{}"),
        transcript=json.loads(row["transcript"] or "[]"),
    )
