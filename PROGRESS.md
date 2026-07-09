# TourneyDesk — PROGRESS

Goal spec: `/home/evan/dev/tournament-scheduler-goal-prompt.md` (mission: conversational tournament
scheduling product on top of the existing CP-SAT solver).

## Milestone checklist

- [ ] **M1 — Agent skeleton**: ClaudeIntake + strict spec-mutation tools + `tourneydesk chat --brief` CLI harness + FakeIntake + fake-LLM e2e test
- [ ] **M2 — Scoreboard**: golden brief corpus (first 15), persona-driven eval runner, metrics JSON, CI gate on deterministic subset
- [ ] **M3 — Web core**: FastAPI + WebSocket chat, live Rules panel, SQLite spec persistence (screenshot evidence)
- [ ] **M4 — Speculative solve**: debounced background solves, Sample Schedule panel, assumption-labeled defaults
- [ ] **M5 — Infeasibility engine**: assumption-instrumented solver, minimal conflict extraction + golden tests, NL explanation + repairs
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
- **In flight** (3 parallel worktree subagents): M1 agent skeleton (sonnet, + core service layer),
  M2 golden brief corpus first 15 (sonnet), M5 solver assumption instrumentation + conflict
  extraction (opus).
- **Next**: review + merge M1 when green; then build M3 web core; merge M2/M5 as they land.
