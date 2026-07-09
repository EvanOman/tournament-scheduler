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
