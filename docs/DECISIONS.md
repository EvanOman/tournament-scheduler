# TourneyDesk — Decision Log

Running log of every product and engineering design decision: **what**, **why**, and **alternatives rejected**.
Append-only; newest at the bottom of each section. Subagents report decisions here via the goal loop.

## Architecture

### D1 — Shared `core` service layer; frontends are thin (2026-07-09)
- **What:** Intake agent, spec store/session, solver service, and the conversation loop live in `tourneydesk/core`. The `tourneydesk chat` CLI and the FastAPI web app are both thin frontends over this one service layer — no parallel implementations.
- **Why:** Evan's hard requirement — CLI/site component parity. The end-goal gate is persona agents driving the real site; the CLI must exercise the identical code path so terminal testing and browser testing validate the same system.
- **Rejected:** A standalone CLI conversation loop separate from the web loop (drift risk; two places to fix bugs).

### D2 — Spec is the single source of truth; agent holds no prose state (2026-07-09)
- **What:** The intake agent never stores tournament facts in prose. Every learned fact is written into a `SpecSession` draft via a strict-schema tool call, with the director's `source_quote` kept as provenance, and echoed back in plain language.
- **Why:** Round-trip contract (spec → NL summary → confirmation) is the anti-hallucination mechanism; provenance powers the Rules panel and the headline "hallucinated-constraint rate = 0" safety metric.
- **Rejected:** Free-form LLM memory of constraints (unauditable, confabulation-prone).

## Engineering

### D3 — Reuse existing worktrees for M1/M2/M5; explicit non-Fable subagent models (2026-07-09)
- **What:** Iteration 2 reuses the three pre-existing worktrees (m1-agent-skeleton, m2-corpus, m5-solver-assumptions). Subagents get explicit models (sonnet for M1/M2 impl, opus for M5's OR-heavy conflict extraction). Never Fable, never inherited.
- **Why:** Goal-prompt §9 HARD CONSTRAINT + Evan's Fable billing guardrail; avoids duplicate worktrees from the interrupted run.

### D4 — Reprioritize M3 (running site) ahead of full M2 corpus (2026-07-09)
- **What:** After M1 merges, build M3 (FastAPI + WebSocket chat + live Rules panel) before completing M2's 15-brief corpus. M2/M5 continue in parallel worktrees; M3 merges next.
- **Why:** Evan's directive — the critical path is a running, browser-usable site for persona testing. A working vertical slice (chat → spec → sample schedule in UI) beats completing all milestones on paper.

### D6 — Single authoritative gate on main after batch merge (2026-07-09)
- **What:** With three disjoint-tree branches (solver / tourneydesk / evals) reviewed and each
  reported green by its worker, merge all three and run ONE full `just check` on merged main as
  the gate, instead of serially re-running the ~8-minute suite per worktree.
- **Why:** Concurrent full suites thrashed each other (SIGTERM'd runs); the merged-main check is
  the integration truth anyway. Review evidence: worker-reported green + zero failures in partial
  independent runs + Fable file-level review, which caught and fixed one real red test
  (`test_fake_e2e` assumption-labeling) before it could land unnoticed.
- **Rejected:** Four sequential full-suite runs (~35 min of wall time for no added signal).

## LLM integration

### D5 — API key verified live; product model `claude-opus-4-8` (2026-07-09)
- **What:** `ANTHROPIC_API_KEY` confirmed working via a 1-token live call to `claude-opus-4-8` (HTTP 200). Live evals unblocked. FakeIntake remains the offline/CI path (no network, no key).
- **Why:** Directive #5 — verify early; fall back to FakeIntake only if the key were dead.

## Web core (M3 + M4)

### D6 — FastAPI + Starlette WebSocket; vanilla TypeScript + Vite frontend (2026-07-09)
- **What:** Backend is FastAPI (already implied by goal-prompt §6). Frontend is **vanilla TypeScript + Vite** with a tiny hand-rolled DOM helper and per-panel render functions — no React/Vue/Svelte.
- **Why:** The three-panel UI is a handful of views driven by a WebSocket event stream; a framework buys little and costs bundle size + a bigger dependency surface (which the 7-day min-release-age makes riskier). Vanilla TS keeps the build to two dev deps (`typescript`, `vite`), both long-stable, and the whole app ships in ~14 kB JS / ~10 kB CSS. `tsc --noEmit` gives strict type safety.
- **Rejected:** React (bundle + churn), htmx (WebSocket streaming of *both* tokens and structured panel state is awkward without client state).

### D7 — Streaming seam extended in `core`, not duplicated (2026-07-09)
- **What:** Streaming assistant tokens flows through the shared service layer: `IntakeProvider.send(msg, on_text_delta=None)` gained an optional sync callback sink. FakeIntake paces text word-by-word; ClaudeIntake forwards the SDK `stream.text_stream`. `IntakeService.send` forwards the sink. The web layer runs one turn via `asyncio.to_thread(asyncio.run, service.send(...))` so the blocking Anthropic SDK never touches the event loop, and the sink hops chunks back with `loop.call_soon_threadsafe`.
- **Why:** Goal-prompt hard requirement — CLI and web share one service layer. Streaming is the only place the web needs more than the CLI, and it is added *behind the existing contract* (default `None` = no streaming), so the CLI is unchanged and both benefit.
- **Rejected:** A parallel async provider just for the web (drift); iterating the sync SDK stream directly on the event loop (blocks it).

### D8 — Speculative solve: `core.SpeculativeSolver`, 1.5s debounce, generation-guarded (2026-07-09)
- **What:** After any turn that mutated the spec, a debounced (1.5s) background solve runs in a worker thread and pushes `solve_started` then `solve_completed`. A monotonic generation counter supersedes stale runs: a newer trigger cancels the pending timer and causes any in-flight solve's result to be dropped. CP-SAT itself isn't interrupted mid-run (it's fast at demo scale); correctness comes from discarding superseded results. Lives in `core` so it is unit-testable and CLI-reusable.
- **Why:** Goal-prompt §3.2 / M4 — the director sees consequences without a solve per keystroke, and never a schedule that lags the latest edit. 1.5s matches the spec's "~1.5s" and feels responsive without thrashing the solver.
- **Rejected:** Solving on every mutation (wasteful, flickery); a cancel token threaded into CP-SAT (complexity unjustified when superseded-result-drop is sufficient).

### D9 — WebSocket protocol shape (2026-07-09)
- **What:** One WS per session at `/ws/{sid}`, JSON both ways. Client sends `{type:"chat",text}`. Server pushes `session_state` (on connect: rules + transcript), `user_message`, `assistant_delta` (token chunk), `assistant_message` (final turn + tool echoes + complete flag), `spec_updated` (full Rules state), `solve_started`, `solve_completed` (schedule payload for *every* status), and `conflict_detected` (extra signal on infeasible/invalid). REST covers list/create/get session, `/spec`, `/schedule`.
- **Why:** Full rules + full schedule are pushed as whole snapshots (not diffs) — boring, idempotent, and trivial for the panels to render wholesale. `solve_completed` carries the status enum so one handler drives solved / waiting / conflict states.

### D10 — Boring SQLite persistence; live conversation in memory (2026-07-09)
- **What:** One `sessions` table (id, title, timestamps, `rules_json`, `transcript` as JSON). The store is written on every turn. The *live* `IntakeService` (and its provider's message history) lives in the in-memory `SessionManager`; REST read views are served from the store so sessions list and render across reloads.
- **Why:** Goal-prompt: "keep it boring." Spec-as-JSON in one row is enough for M3. Rehydrating a fully live provider (with Claude message history) across a process restart is out of scope for the slice — a restarted process shows the stored rules/transcript read-only and starts a fresh live conversation (known gap, noted in `web/store.py`).

### D11 — Palette & typography: "floodlit night-match operations console" (2026-07-09)
- **What:** A committed dark surface (`#0d1017` bg, `#151b26` panels), **turf-green** signal (`#34d17e`), **whistle-amber** accent (`#f5b544`), chalk-white text. Uppercase tracked kickers; a scoreboard **monospace** for all numbers (times, stats). Divisions are coloured with the **Okabe-Ito** colourblind-safe palette. A turf→amber hairline under the top bar is the signature.
- **Why:** Goal-prompt §6 — distinctive, not AI-slop (explicitly *not* Inter-on-purple-gradient), suited to a sports-operations tool a director runs a tournament from. Okabe-Ito satisfies the colourblind-safe division-colour requirement.
- **Rejected:** A light "chalkboard" theme toggle (unneeded for the slice; a single deliberate look reads more like a real ops tool).

### D12 — Built frontend assets committed under `tourneydesk/web/static/` (2026-07-09)
- **What:** Vite builds into `tourneydesk/web/static/`, which FastAPI serves via `StaticFiles(html=True)` mounted last (so `/api` and `/ws` win). The built `index.html` + hashed assets are committed. `just check` runs `frontend-check` (typecheck + build) but skips gracefully when `npm` is absent.
- **Why:** Goal-prompt — the server must run without Node at deploy time. Committing the bundle makes `uv run tourneydesk serve` self-sufficient; the node guard keeps `just check` green on a Python-only box.

## D13 — Session-per-visit with URL-hash rejoin (2026-07-09, persona blocker)
The frontend joined `sessions[0]` unconditionally, so concurrent visitors landed in one shared
conversation — the 6-persona validation fleet cross-contaminated each other's specs (P1/P2/P4
all reported phantom tournaments). Now every page load creates a fresh session unless the URL
hash carries `#s=<id>`; we set the hash after creating, so reload rejoins the same session.
Alternative rejected: cookie-pinned sessions (breaks multi-tab-as-multi-director demos and
shareable links).

## D14 — Tool-error results are model-facing only (2026-07-09, persona finding)
Error ToolResults (validation failures, missing args) were echoed into the UI provenance chips,
so directors saw internals like `'source_quote'` (a bare KeyError repr) and "No division 'u10'
to remove." Errors now go only back to the model as tool_result feedback; only successful
mutations render as chips. KeyError messages were also rephrased to name the tool and missing
argument (more important now that schemas are non-strict — see D-strict note in tools.py).

## D15 — Known product-model gap (queued): field_size ontology conflates field dimensions with
game format. Vic (P2) said "U10 plays 8v8 on the small fields" but the enum maps small→"4v4/3v3".
Fix direction: per-division format string decoupled from field size. Not blocking; queued for a
follow-up branch with the M5 explanation work.

## D16 — Strict tools rationed to an empirical grammar budget (2026-07-09, eval-runner lane)
Live eval canaries exposed three successive API rejections of the tool suite: (1) nullable
enum unions are invalid in strict schemas; (2) >16 union-typed params across the suite is
rejected outright; (3) the compiled-grammar size cap is TIGHTER with adaptive thinking on —
probing showed at most 5 of our tools can be strict (claude-opus-4-8, 2026-07-09). Resolution
supersedes D-strict/d46c5b2's all-non-strict fallback: the 5 highest-risk mutation tools
(add_division, update_division, add_teams, add_field, set_field_availability) are strict, with
the division tools converted to sentinel optionals (-1 / 'unspecified' / 'unchanged' — decoded
back to None in dispatch) to stay union-free. `tests/test_tools.py::TestSchemas` enforces the
split and both budgets. Rejected: all-strict (API rejects), all-non-strict (gives up
constrained decoding exactly where malformed calls are most likely).

## D17 — Facts-scoped eval scoring (2026-07-09, eval-runner lane)
A constraint the persona cannot state must not be scored. 13/15 corpus briefs have golden team
rosters the `facts` never enumerate, and golden `bracket_after_pools` is just the model default
when facts never mention playoffs — strict comparison tanked b01 to F1=0.14 for reasons no
agent could fix. `score_spec` now takes `facts_text`: team-name matching applies only to names
present in facts (others compare by count); `bracket_after_pools` is compared only when facts
mention bracket/playoff keywords. Divisions match by id with normalized-name fallback (agent-
derived ids like 'u9c' vs golden 'u9' are implementation details), cascading through teams and
preference targets. Golden self-checks (with and without facts) gate drift. After these fixes,
live smoke: b01/b03/b15 all F1=1.000, hallucinated=0 — including the adversarial bait canary.

## D18 — Eval results JSON are committed evidence (2026-07-09, eval-runner lane)
`evals/results/*.json` are NOT gitignored: goal prompt §4 requires every eval run recorded with
model id, prompt version (sha256 of prompts.py, 12 hex), and per-brief breakdown, with trends
tracked over time. Rejected: ignoring them as build artifacts (kills trend tracking and makes
eval claims unauditable).
