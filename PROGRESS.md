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

- `ANTHROPIC_API_KEY` is set in the environment → live LLM calls and evals are unblocked.
- Product model default `claude-opus-4-8` (adaptive thinking); `TOURNEYDESK_MODEL` env override;
  `claude-fable-5` requires omitting `thinking` and handling `stop_reason == "refusal"`.
- Ports: bind 18780+ only; never kill occupied ports. Package installs: 7-day min-release-age, never override.

## Iteration log

### Iteration 1 — 2026-07-03

- **Attempted**: Repo survey; baseline `just check`; wrote shared conventions (`docs/DESIGN.md`);
  fanned out three worktree subagents: M1 agent skeleton, M2 golden brief corpus, M5 solver
  assumption instrumentation.
- **Verified**: (pending — see below)
- **Evidence**: (pending)
- **Next**: merge worktrees when green; then M2 eval runner wiring.
