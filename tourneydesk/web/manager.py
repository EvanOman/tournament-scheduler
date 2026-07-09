"""In-memory registry of live conversations, backed by the SQLite store.

Each browser session maps to one `LiveSession` holding the exact same
`IntakeService` the CLI drives -- structural CLI/web parity (DECISIONS D1). The
manager owns the provider factory (Claude for real use, Fake for offline demos
and tests) and the durable store; the WebSocket handler in `app.py` borrows a
`LiveSession` and layers streaming + speculative-solve orchestration on top.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tourneydesk.core.service import IntakeService, SolveOutcome
from tourneydesk.providers.base import IntakeProvider
from tourneydesk.session import SpecSession
from tourneydesk.web.store import SessionRow, SessionStore

ProviderFactory = Callable[[SpecSession], IntakeProvider]


@dataclass
class LiveSession:
    """A conversation held in memory: the service plus its running transcript."""

    id: str
    service: IntakeService
    transcript: list[dict[str, Any]] = field(default_factory=list)
    last_outcome: SolveOutcome | None = None

    @property
    def session(self) -> SpecSession:
        return self.service.session


class SessionManager:
    """Creates, tracks, and hands out live sessions; mirrors state to the store."""

    def __init__(self, store: SessionStore, provider_factory: ProviderFactory) -> None:
        self._store = store
        self._factory = provider_factory
        self._live: dict[str, LiveSession] = {}

    @property
    def store(self) -> SessionStore:
        return self._store

    def create(self, title: str = "Untitled tournament") -> SessionRow:
        row = self._store.create(title)
        self._spawn_live(row.id)
        return row

    def _spawn_live(self, sid: str) -> LiveSession:
        session = SpecSession()
        provider = self._factory(session)
        live = LiveSession(id=sid, service=IntakeService(provider))
        self._live[sid] = live
        return live

    def live(self, sid: str) -> LiveSession | None:
        """Return the live session, spawning a fresh one if the id exists but isn't loaded.

        A restart (or a session created before this process started) yields an
        empty live conversation; the stored rules/transcript remain visible via
        the read-only `get`/`list` views.
        """
        if sid in self._live:
            return self._live[sid]
        if self._store.get(sid) is None:
            return None
        return self._spawn_live(sid)

    def record_turn(self, live: LiveSession, director_message: str, agent_text: str, echoes: list[str]) -> None:
        live.transcript.append({"role": "director", "text": director_message})
        live.transcript.append({"role": "agent", "text": agent_text, "echoes": echoes})
        self._store.save_state(
            live.id,
            rules=live.session.to_rules_json(),
            transcript=live.transcript,
            title=live.session.name or None,
        )

    def get_row(self, sid: str) -> SessionRow | None:
        return self._store.get(sid)

    def list_rows(self) -> list[SessionRow]:
        return self._store.list_all()
