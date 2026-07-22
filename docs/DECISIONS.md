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

## D16 — game_format decoupled from field_size (2026-07-09, persona round 2, CRITICAL)
P2 proved the field_size→format gloss isn't cosmetic: "U10 plays 8v8" was stored as medium,
displayed "7v7", was uncorrectable, and the size auto-categorization left U10 zero eligible
fields → hard solver failure. DivisionSpec now carries `game_format` (verbatim, display/record
only); field_size remains the eligibility gate; every "small→4v4/3v3"-style gloss removed from
tools, prompt, and UI. Rejected alternative: format-driven field matching (formats vary by
region/club; the physical field is what the solver actually needs).

## D17 — Speculative solves clamped to 10s + honest "inconclusive" status (P4)
Repair turns fire several mutations; 60s-budget solves stacked into a 3+ min "SOLVING…" hang
over a stale conflict banner. Speculative path now clamps max_solve_seconds to 10 and maps
CP-SAT UNKNOWN to a new `inconclusive` status with honest copy; the frontend shows a re-solving
state instead of a stale conflict while a solve runs.

## D18 — get_schedule_summary: the agent can see the schedule (P5)
Under interrogation the agent deflected ("I can't see the preview") and once confabulated a
"stale preview" excuse contradicted by visible UI state. New read-only tool solves the current
draft (same clamp) and returns per-field/per-team-by-day facts; prompt rule 7 forbids answering
schedule questions from memory; rule 8 requires honesty about soft-preference enforcement.

## D19 — Intake wrap-up gated on schedulability (P2)
The agent said "intake complete — good luck!" over a failing schedule. Prompt rule 5 now forbids
closing out while the draft is known unschedulable.

## D20 — Per-mutation spec push + verified-claims rule (persona round 3, P4)
During long multi-tool turns the streamed chat claimed fixes had landed while the Rules/Schedule
panels sat stale until turn end (90s+ contradiction). Providers now fire an on_spec_mutated
callback after every successful mutation; the web layer pushes spec_updated and re-arms the
speculative solver mid-turn. Prompt rule 9 forbids asserting "fixed/OPTIMAL" without a fresh
get_schedule_summary after the mutations.

## D21 — Derived values need director confirmation + preference removal tools (round 3, P1)
The agent invented a 9AM–1PM window to encode "finish earlier" and attributed it to the
director's quote — and had NO tool to remove a bad preference once recorded. Prompt rule 2 now
requires proposing derived values and using the director's confirmation as the quote;
remove_time_preference / remove_field_preference added.

## D22 — Queued (solver objective, M6 lane): pack-first objective abandons available fields
(P1: field restricted by 1h → 0 games; P5/P2: 20/20/8/0 imbalance). Needs a spread/idle-field
penalty term in the CP-SAT objective + making the "use all my fields" ask expressible.

## D23 — Memoized solve keyed by spec fingerprint; digest gains game-level data (round 3, P5)
CP-SAT is nondeterministic across runs, so the digest tool's independent re-solve could describe
a different (equally optimal) schedule than the panel rendered — manufacturing a "stale panel"
dispute. `solve_current(session)` memoizes on the clamped spec's JSON fingerprint; the panel's
speculative solve and the agent's digest now always describe the same solution. The digest also
lists every game per team (day, time, opponent, field) so the agent never asserts matchup facts
it cannot see, and prompt rule 7 scopes claims to summary contents.

## D24 — Turn crash-proofing: dispatch never raises, history integrity guaranteed (round 4, P4 blocker)
A combined two-change message crashed the turn instantly and permanently (4/4 retries): an
exception escaping dispatch() mid-tool-loop left the conversation history with a dangling
tool_use (no tool_result), 400-ing every later request on that session. dispatch() now has a
final broad except returning an actionable is_error result, and the provider's tool loop
appends error tool_results for any unanswered tool_use in a finally block — the transcript can
no longer be corrupted by a single bad turn.

## D25 — Duplicate team names rejected; team ids surfaced in the spec summary (round 4, P4)
"Dave coaches Team 1/2/3" re-created three new teams with those names (27-team roster), which
rendered as an apparent "Team 2 v Team 2" self-match (two distinct ids sharing a name).
add_teams now rejects a duplicate name within a division with a pointer to the existing team's
id, and get_spec_summary lists every team as "name [id]" so existing placeholders are
referenceable instead of accidentally recreated.
## D26 — Empirical strict-schema budget findings; main stays all-non-strict (eval-runner lane)
Live eval canaries measured the API's strict-tool limits precisely: (1) nullable enum unions
are invalid in strict schemas; (2) >16 union-typed params across the suite is rejected;
(3) the compiled-grammar cap is TIGHTER with adaptive thinking on — at most 5 of our tools can
be strict (claude-opus-4-8, 2026-07-09). The lane prototyped a 5-strict-tools-with-sentinel-
optionals scheme (preserved on branch `m2-eval-runner`), but main keeps the whole suite
non-strict: the persona-fix evolution of tools.py (game_format, preference removals, digest,
crash-proofing D24) is validated in production, dispatch() covers validation locally, and the
sentinel encoding would complicate every optional field. Revisit only if malformed tool calls
show up as a real error class in eval results.

## D27 — Facts-scoped eval scoring (2026-07-09, eval-runner lane)
A constraint the persona cannot state must not be scored. 13/15 corpus briefs have golden team
rosters the `facts` never enumerate, and golden `bracket_after_pools` is just the model default
when facts never mention playoffs — strict comparison tanked b01 to F1=0.14 for reasons no
agent could fix. `score_spec` now takes `facts_text`: team-name matching applies only to names
present in facts (others compare by count); `bracket_after_pools` is compared only when facts
mention bracket/playoff keywords. Divisions match by id with normalized-name fallback (agent-
derived ids like 'u9c' vs golden 'u9' are implementation details), cascading through teams and
preference targets. Golden self-checks (with and without facts) gate drift. After these fixes,
live smoke: b01/b03/b15 all F1=1.000, hallucinated=0 — including the adversarial bait canary.

## D28 — Eval results JSON are committed evidence (2026-07-09, eval-runner lane)
`evals/results/*.json` are NOT gitignored: goal prompt §4 requires every eval run recorded with
model id, prompt version (sha256 of prompts.py, 12 hex), and per-brief breakdown, with trends
tracked over time. Rejected: ignoring them as build artifacts (kills trend tracking and makes
eval claims unauditable).

## D29 — Subscription-billed provider via Claude Agent SDK (2026-07-12)
The persona campaign exhausted API credits because product turns (ClaudeIntake → raw anthropic
SDK) pick up ANTHROPIC_API_KEY (exported in ~/.zshrc, baked into the service env). New default
provider AgentSDKIntake drives the Claude Agent SDK (bundled Claude Code binary, OAuth login) —
turns bill the owner's Max subscription. Verified live with a zero-credit API account.
POLICY BOUNDARY: personal/dev use by the account owner only — Anthropic does not permit offering
claude.ai login/limits to a product's users (incl. Agent SDK agents); anything user-facing runs
TOURNEYDESK_PROVIDER=api. Key scrubbed from the process env on provider construction (Python
ClaudeAgentOptions.env merges, so popping the parent env is the only reliable scrub);
setting_sources=[] so local CLAUDE.md never leaks into product turns; tools=[] +
allowed_tools=["mcp__spec__*"] restricts the agent to the 19 spec tools.

## D30 — `/chat/stream` SSE: additive, frozen event contract, no new dependency
The demo's 20-40s turn was completely silent (one dict back after the whole agentic loop + a
CP-SAT solve). Added `POST /chat/stream`, additive next to the untouched `/chat`, so a second
team could build the frontend against a contract fixed in advance rather than against a moving
target. Frames are hand-formatted (`"event: <name>\ndata: <single-line JSON>\n\n"`) — no
`sse-starlette` dependency; FastAPI's `StreamingResponse` over an async generator is enough.

Event contract: zero or more `status` (one per successful spec mutation, reusing the existing
`dispatch()` echo text `PydanticAIIntake` already produced for `/chat`'s `reply`, plus one
"Solving your schedule…" immediately before the solve step), then zero or more `delta` (true
assistant-reply token chunks), then exactly one terminal frame — `final` (mirrors `/chat`'s
`{session_id, reply, rules, schedule}` body) on success, or `error` on failure. Note the
"Solving…" status is emitted AFTER the delta events, not grouped with the mutation statuses
before them — it fires once the turn (tool calls + assistant reply) is fully done, which is also
when the solve becomes safe to run under the same crash-proofing boundary `/chat` already has (a
solve exception falls back to an `{status: "incomplete"}` schedule instead of an `error` frame).

Provider (`PydanticAIIntake.send`): added a keyword-only `on_progress` callback, fired alongside
the existing `on_spec_mutated` in `_make_tool_fn`'s success-mutation branch (same
`_Deps`-threaded plumbing). When a caller passes `on_text_delta`, `send` now runs
`Agent.run_stream(...)` + `stream_text(delta=True, debounce_by=None)` for real token deltas of
the FINAL text instead of `Agent.run(...)`'s single full-text callback; `run_stream` still
executes the complete tool loop first (same `deps`, same `dispatch()`), so tool-call semantics
are identical to the non-streaming path. When `on_text_delta` is `None` (i.e. `/chat`), `send`
takes the original `Agent.run()` branch UNCHANGED — `/chat`'s behavior and tests are untouched.

Threading: Pydantic AI runs sync tool functions (`_make_tool_fn`'s `fn`) via `anyio.to_thread`,
a real worker thread — confirmed by reading `_function_schema.py`'s `FunctionSchema.call`. So
`on_progress` fires off the event loop thread and MUST hop back via
`loop.call_soon_threadsafe(queue.put_nowait, ...)`; `on_text_delta`, by contrast, fires from
`stream_text`'s async iteration on the event loop itself. The endpoint uses
`call_soon_threadsafe` uniformly for both — it is safe (if slightly redundant) when already on
the loop, and it means one `emit()` helper instead of two threading-aware code paths.

Rejected: computing/solving concurrently with the reply-text stream (kicking off `solve_current`
as soon as the last tool call lands, since the session is already mutated by then) — it would
let the "Solving…" status arrive before the deltas, matching the contract's listed event order
more literally, but it doubles the in-flight work per chat turn on a single-worker `_EXECUTOR`
sized for a free-tier demo instance, for no user-visible benefit over the simpler sequential
bridge.

## D31 — Modal memory snapshots for the demo app (2026-07-17)

`deploy/modal_app.py` sets `enable_memory_snapshot=True`: scale-from-zero restores from a
memory snapshot instead of re-importing Python, cutting measured cold starts from ~8-12 s to
a couple of seconds (the linprogx demo made the same change: 21.7 s → ~6 s worst case).
Snapshot-safety was verified before enabling: the module-level pydantic-ai `_AGENT` binds no
model, and `OpenAIProvider` HTTP clients + API keys are constructed lazily per run
(`_build_model`), so nothing holds a socket across the snapshot. The first wake after each
deploy is the snapshot-creating boot (still slow); every later wake restores.

## D32 — Scheduled warm window for the Modal demo (2026-07-18)

Two cron functions in `deploy/modal_app.py` bound the demo's cold-start exposure without
paying for 24/7 warmth: `keep_warm` (hourly, weekdays 8:00-19:00 America/Chicago) sets
`min_containers=1` via `update_autoscaler()`, `wind_down` (weekdays 20:00) sets it back
to 0. The window covers US business hours — 9am Eastern through 6pm Pacific, Mon-Fri. A
warm 1-CPU/1-GiB container reserves ~$0.055/h (Modal pricing 2026-07), so the ~60 h/week
window is ~$14/month nominal — comfortably inside the Starter plan's $30/month credits,
where always-warm (~$40/month) is not. keep_warm re-asserts hourly rather than once
because a redeploy resets the autoscaler to the decorator's `min_containers=0`; the hourly
tick self-heals within the hour. Off-window visitors (nights/weekends) get the ~8-10 s
snapshot cold start, usually hidden by the site's warm-on-load pings — an accepted worst
case. Rejected: 24/7 `min_containers=1` (cost) and slimming the image (marginal — the
restore is dominated by ortools + pydantic-ai regardless).

## D33 — Extend evening warmth and defer LLM provider imports (2026-07-21)

Production traces recorded a TourneyDesk health warmup at 21:52 America/Chicago taking
7.97 seconds, after D32's 20:00 wind-down. The weekday warm window now runs 8:00-23:00:
`keep_warm` re-asserts every 15 minutes through 22:45 and `wind_down` runs at 23:00.
The shorter reconciliation cadence bounds deploy-induced cold gaps during the final hour
without adding reserved container time. The longer window adds 15 container-hours/week
(about $3.50/month at the D32 rates), bringing the nominal total to roughly $18/month
while retaining scale-to-zero overnight and on weekends.

The demo API also no longer imports Anthropic or Pydantic AI on its health and ordinary
solver startup paths. Provider package exports remain compatible through a lazy
`ClaudeIntake` attribute; web package exports no longer initialize the provider stack as a
side effect of importing a lightweight web helper; the chat adapter loads when the first
chat session is created; and conflict-explanation code loads only for an infeasible solve,
with the Anthropic SDK
itself deferred until the explicitly requested LLM explanation path. This preserves chat
and deterministic explanation behavior while reducing snapshot creation and fallback cold
starts. Rejected: permanent warm capacity, which still exceeds the personal-demo need and
the Starter credit envelope.
