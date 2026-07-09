"""Debounced, cancellable speculative solve orchestration.

Whenever the draft spec changes, the web layer wants to show the director the
*consequences* of what they've said -- a fresh sample schedule -- without
solving on every keystroke or blocking the event loop while CP-SAT runs. This
class encapsulates that policy so it lives in `core` (testable, reusable) rather
than being tangled into the WebSocket handler:

* **Debounced.** `trigger()` schedules a solve `debounce_seconds` in the future;
  a second `trigger()` inside that window resets the timer. Rapid-fire spec
  mutations collapse into one solve.
* **Cancellable / no stale results.** Each trigger bumps a generation counter.
  A solve whose result comes back after a newer trigger fired is dropped -- the
  director only ever sees the latest. The pending timer is cancelled outright.
* **Non-blocking.** The (blocking) CP-SAT solve runs in a worker thread via
  `asyncio.to_thread`, so the event loop keeps serving the WebSocket.

The class knows nothing about WebSockets or specs: it is driven entirely by the
injected `solve_fn` (returns a `SolveOutcome`) and the async `on_started` /
`on_result` callbacks. That keeps it a pure scheduling primitive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from tourneydesk.core.service import SolveOutcome

SolveFn = Callable[[], SolveOutcome]
OnStarted = Callable[[], Awaitable[None]]
OnResult = Callable[[SolveOutcome], Awaitable[None]]


class SpeculativeSolver:
    """Coalesces spec-change triggers into debounced background solves."""

    def __init__(
        self,
        solve_fn: SolveFn,
        on_started: OnStarted,
        on_result: OnResult,
        debounce_seconds: float = 1.5,
    ) -> None:
        self._solve_fn = solve_fn
        self._on_started = on_started
        self._on_result = on_result
        self._debounce = debounce_seconds
        self._generation = 0
        self._task: asyncio.Task[None] | None = None

    def trigger(self) -> None:
        """Request a solve. Resets the debounce timer and supersedes any pending run."""
        self._generation += 1
        generation = self._generation
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._run(generation))

    async def _run(self, generation: int) -> None:
        try:
            await asyncio.sleep(self._debounce)
        except asyncio.CancelledError:
            return
        if generation != self._generation:
            return
        await self._on_started()
        outcome = await asyncio.to_thread(self._solve_fn)
        # Drop a result the director has already moved past.
        if generation != self._generation:
            return
        await self._on_result(outcome)

    async def aclose(self) -> None:
        """Cancel any pending solve; safe to call on WebSocket disconnect."""
        self._generation += 1
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
