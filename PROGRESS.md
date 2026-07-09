# TourneyDesk — PROGRESS

Goal spec: `/home/evan/dev/tournament-scheduler-goal-prompt.md` (mission: conversational tournament
scheduling product on top of the existing CP-SAT solver).

## Milestone checklist

- [x] **M1 — Agent skeleton**: ClaudeIntake + strict spec-mutation tools + `tourneydesk chat --brief` CLI harness + FakeIntake + fake-LLM e2e test *(merged; full gate green 2026-07-09)*
- [ ] **M2 — Scoreboard**: golden brief corpus ✅ (15 briefs merged) · persona-driven eval runner + metrics JSON + CI gate ⏳ (in flight, `m2-eval-runner`)
- [ ] **M3 — Web core**: FastAPI + WebSocket chat, live Rules panel, SQLite spec persistence (screenshot evidence)
- [ ] **M4 — Speculative solve**: debounced background solves, Sample Schedule panel, assumption-labeled defaults
- [ ] **M5 — Infeasibility engine**: instrumented solver + minimal conflict extraction ✅ (merged) · NL explanation + repairs ⏳
- [ ] **M6 — Adjustment loop**: minimal-churn re-solve, schedule diff view, disruption briefs
- [ ] **M7 — Explanation & brackets**: explanation bundle, solver-grounded "why" answers, bracket phase
- [ ] **M8 — Polish & full corpus**: 25+ briefs, eval trend doc, README refresh, design polish, tag v0.2.0

## Environment facts

- `ANTHROPIC_API_KEY` is set AND verified live (2026-07-09): 1-token call to `claude-opus-4-8`
  returned HTTP 200. Live LLM calls and evals are unblocked. FakeIntake is the offline/CI path.
- Product model default `claude-opus-4-8` (adaptive thinking); `TOURNEYDESK_MODEL` env override;
  `claude-fable-5` requires omitting `thinking` and handling `stop_reason == "refusal"`.
- Ports: bind 18780+ only; never kill occupied ports. Package installs: 7-day min-release-age, never override.

## Iteration log

### Iteration 1 — 2026-07-03

- **Attempted**: Repo survey; baseline `just check`; wrote shared conventions (`docs/DESIGN.md`);
  fanned out three worktree subagents. Prior run died on a spend limit before any worker produced code.
- **Result**: No product code landed. Superseded by iteration 2.

### Iteration 2 — 2026-07-09

- **Directives folded in** (from Evan): (1) CLI + web must share ONE `tourneydesk/core` service
  layer — thin frontends only; (2) maintain `docs/DECISIONS.md`; (3) after M1, prioritize M3
  (running site) ahead of full M2 corpus — a browser-usable site is the critical path;
  (4) do not self-deploy — report launch instructions; (5) verify API key early.
- **Verified so far**: main `just check` green at base; `ANTHROPIC_API_KEY` live (opus-4-8, HTTP 200);
  m1 worktree's `anthropic>=0.112.0` dep addition inspected and kept (published 2026-06-24, past
  the 7-day min-release-age).
- **Landed on main** (all three worktree streams, Fable-reviewed before/at merge):
  - **M5 solver instrumentation** (`cee5e4d`, merged `c2bc431`): `build_model(instrument=True)`
    guards all 6 hard-constraint families behind per-entity assumption literals;
    `tournament_scheduler/conflict.py` extracts deletion-filter minimal unsat cores with
    plain-English descriptors. 13 golden conflict tests; all 3 golden infeasible specs produced
    proven-minimal cores naming the true culprit (rest/field-capacity/coaching). Independent
    `just check` in the worktree: exit 0, 54 passed.
  - **M1 agent skeleton** (`99735ed` + fix `570cc49`, merges `62113a9`/`f4b571c`): `tourneydesk/`
    package — SpecSession with provenance + labeled assumptions, 17 strict-schema mutation tools,
    ClaudeIntake (opus-4-8, adaptive thinking, cached prompt, refusal handling) + FakeIntake,
    `IntakeService` core layer (CLI/web parity by construction), `tourneydesk chat` CLI.
    44 offline tests incl. fake-LLM e2e (chat → tools → spec → solve → validate). Review caught a
    real red test on the headline e2e (assumption labeling vs `bracket_after_pools: None` script
    input) — fixed to assert the exact labeled assumption instead.
  - **M2 golden corpus** (`d822526`, merged `4930538`): 15 briefs b01–b15 (clean → adversarial
    hallucination-bait canary), 13 feasible golden specs that solve+validate, 2 infeasible briefs
    with quantified golden conflicts; `tests/test_corpus.py` gates it.
- **Note**: a concurrent session merged the three branches into main at 13:04 while this loop was
  mid-review; this loop then merged the outstanding e2e fix and re-gated main with a full
  `just check`.
- **Post-merge gate (main session)**: full `just check` on merged main at `dbee8b1` — exit 0,
  **217 passed, 2 skipped** (7:25). Main verified.
- **Single-writer protocol established** after the concurrent-merge race: the Fable main session
  owns main (merges + these docs); the goal-loop orchestrator and all workers commit only on
  worktree branches and report "READY TO MERGE".
- **In flight**: M3 web core (opus worker, `m3-web-core` worktree — FastAPI/WS + 3-panel frontend,
  port 18780); M2 eval runner (orchestrator, `m2-eval-runner` worktree). After M3 merge: deploy via
  deploy skill, then 6-persona browser validation fleet (P1–P6, defined in session scratchpad).
