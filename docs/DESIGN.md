# TourneyDesk — Design

Architecture and contracts for the conversational tournament-scheduling product built around the
CP-SAT solver in `tournament_scheduler/`. Keep this current; subagents treat it as the source of
truth for cross-cutting conventions.

## 1. Big picture

```
Director ⇄ Chat (web/CLI) ⇄ Intake agent (Claude, tool calls only)
                                   │ typed spec mutations
                                   ▼
                           SpecSession (draft state + provenance)
                                   │ to_spec() when complete-enough (defaults labeled as assumptions)
                                   ▼
                    tournament_scheduler: pools → CP-SAT solve → validator → renderer
                                   │
                    speculative schedule / conflict sets / explanation bundle → UI
```

The spec is the single source of truth. The agent never holds tournament facts in prose — every
fact it learns is written into the draft via a strict-schema tool call and echoed back to the
director in plain language.

## 2. Package layout

- `tournament_scheduler/` — solver core. Public API unchanged.
- `tourneydesk/` — product: agent, tools, session, providers, CLI, (later) FastAPI server.
  - `tourneydesk/session.py` — `SpecSession`: mutable draft of a `TournamentSpec` with per-fact
    provenance (the director quote that caused each mutation). Serializes to JSON for the UI Rules
    panel. `to_spec()` materializes a valid `TournamentSpec`, filling labeled defaults
    (`assumptions: list[str]`) for missing optional fields; raises `IncompleteSpecError` listing
    missing required facts otherwise.
  - `tourneydesk/tools.py` — tool definitions (strict JSON schema, `additionalProperties: false`,
    prescriptive when-to-call descriptions) + a dispatcher mapping tool calls onto `SpecSession`.
    Validation failures return `is_error` tool results with actionable messages.
  - `tourneydesk/providers/` — `ClaudeIntake` (official `anthropic` SDK) and `FakeIntake`
    (deterministic scripted tool calls; zero network). Both drive the same tools/dispatcher.
  - `tourneydesk/persona.py` — simulated director for CLI harness and evals (separate Claude call,
    persona-prompted from a brief file).
  - `tourneydesk/cli.py` — `tourneydesk chat [--brief FILE] [--provider claude|fake]` and
    `tourneydesk serve [--port 18780] [--provider claude|fake]`.
  - `tourneydesk/core/speculative.py` — `SpeculativeSolver`: debounced (1.5s), generation-guarded
    background solve orchestration driven by injected `solve_fn` + async emit callbacks. Reusable by
    any frontend; the web layer wires it to WebSocket pushes.
  - `tourneydesk/web/` — FastAPI frontend over the shared service (M3+M4). `app.py` (REST + per-session
    WebSocket + static SPA mount), `manager.py` (in-memory live sessions + provider factory),
    `store.py` (boring SQLite: one row per session, spec/transcript as JSON), `schedule_view.py`
    (SolveOutcome → UI payload: per-field + per-team grids), `canned.py` (offline demo script).
    Built SPA assets are committed under `tourneydesk/web/static/`.
- `evals/` — golden briefs, runner, results (`evals/results/*.json`), trend doc.
- `frontend/` — Vite + vanilla-TypeScript SPA (three panels: Chat / Rules / Schedule). Builds into
  `tourneydesk/web/static/`. Backend binds port **18780** by default (auto-bumps if taken; never
  kills). WebSocket protocol + streaming seam documented in DECISIONS D7–D10.

## 3. LLM integration conventions

- Official `anthropic` Python SDK only. Model from `TOURNEYDESK_MODEL`, default `claude-opus-4-8`
  with `thinking={"type": "adaptive"}`. Never set `temperature`/`top_p`/`top_k`.
- `claude-fable-5`: omit the `thinking` param entirely; handle `stop_reason == "refusal"` (check
  before reading `content`).
- Prompt caching: frozen system prompt as a text block list with
  `cache_control={"type": "ephemeral"}` on the last block; no timestamps/UUIDs in the system
  prompt; per-conversation history appended after.
- Tools: `strict: True` top-level on each tool def; schemas have `additionalProperties: False` and
  full `required` lists. Spec mutations are the *only* way the agent changes state.
- Streaming via `client.messages.stream(...)`; use `get_final_message()` when events are not
  surfaced. Log token usage per conversation (`usage.input_tokens` etc.).
- Structured (non-conversational) extraction steps use `output_config={"format": {"type":
  "json_schema", "schema": ...}}`.

## 4. Spec-mutation tool suite (v1)

Names and semantics (all operate on the `SpecSession` draft; every call takes a `source_quote`
string — the director's words that justify the mutation — stored as provenance):

- `set_tournament_info(name, description?)`
- `add_division(id, name, field_size, game_duration_minutes, ...optional overrides)`
- `update_division(id, ...partial)` / `remove_division(id)`
- `add_teams(division_id, teams: [{id?, name, club?, seed?}])` — ids auto-derived from names when
  omitted; `set_team_count(division_id, count)` for "12 teams in U10" before names are known
  (generates placeholder teams, labeled as assumptions).
- `add_field(id, name, size, availability: [{start, end}])` / `update_field` / `remove_field`
- `set_field_availability(field_id, availability)` — replaces windows.
- `add_coaching_conflict(coach_name, team_ids)` / `remove_coaching_conflict(coach_name)`
- `add_team_avoidance(team_ids[2], reason)` / `remove_team_avoidance(team_ids)`
- `add_time_preference(target, target_type, windows, priority)` / `remove_time_preference(...)`
- `add_field_preference(target, target_type, field_ids, priority)` / `remove_field_preference(...)`
- `get_spec_summary()` — returns the current draft as structured JSON + NL summary (for round-trip
  confirmation).
- `mark_intake_complete(confirmation_quote)` — director has confirmed the summary.

Datetimes are ISO 8601 strings in tool schemas (`YYYY-MM-DDTHH:MM`). The agent resolves relative
dates ("Saturday") by asking, never guessing, unless the brief pins dates.

## 5. Eval brief format (shared contract)

One directory per brief: `evals/briefs/<id>/` containing:

- `brief.yaml`:
  ```yaml
  id: b01_clean_small          # snake_case, sortable
  title: "Clean 24-team fall classic"
  difficulty: easy             # easy | medium | hard | adversarial
  categories: [clean]          # clean, rambling, contradictory, missing_info,
                               # reversal, unit_confusion, infeasible, disruption
  persona: |
    <how the simulated director talks: terse/chatty/distractible/self-contradicting,
     and any behavioral quirks>
  facts: |
    <ground truth the director knows, in prose — the ONLY source the persona may draw on>
  golden_questions:            # clarifying questions the agent SHOULD ask (empty list if none)
    - about: dates             # short key used for scoring
      note: "brief never states which weekend"
  expect_infeasible: false
  golden_conflict: null        # for infeasible briefs: {summary: str, involves: [constraint descriptors]}
  ```
- `golden_spec.yaml` — a valid `TournamentSpec` (must load via `spec_io.load_spec` and, for
  feasible briefs, solve + validate cleanly). Omitted only when `expect_infeasible` and the golden
  spec itself is the infeasible spec (still must *parse*).

Scoring compares the final `SpecSession` spec to `golden_spec.yaml` per constraint category:
divisions, teams, fields/availability, coaching_conflicts, team_avoidances, time_preferences,
field_preferences, division scheduling params. Hallucinated-constraint = any constraint in the
final spec with no support in `facts`.

## 6. Conflict extraction (M5)

- Instrumented solve mode: each hard-constraint *group* (per-matchup slot assignment, field
  compatibility, rest windows, coach conflicts, availability, avoidances) registered behind CP-SAT
  assumption literals via `model.add(...).only_enforce_if(lit)` + `add_assumptions`.
  Fast path (no assumptions) remains the default for speculative solves.
- On INFEASIBLE: `solver.sufficient_assumptions_for_infeasibility()` gives an unsat core; shrink
  toward minimality with the deletion filter (drop one assumption, re-solve, keep the drop if
  still infeasible) — the standard destructive MUS algorithm (Marques-Silva & Lynce 2011; van
  Loon's deletion filter, 1981/Chinneck 2008). Time-boxed; a non-minimal core is acceptable
  fallback.
- Output: `ConflictSet` JSON — list of human-readable constraint descriptors with the spec objects
  involved, suitable for NL translation and golden-set assertions.

## 7. Engineering standards

- pystd: uv, ruff, ty, pytest; `just check` green before every commit; conventional commits.
- `FakeIntake` end-to-end path (chat → tools → spec → solve → validate) must pass with no network
  and no `ANTHROPIC_API_KEY`.
- Solver perf gates: `pytest -m perf` — <5s small, <30s medium, <5min large.
- Never bind ports below 18780; never kill processes on occupied ports.
