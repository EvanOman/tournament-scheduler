# TourneyDesk — PROGRESS

Goal spec: `/home/evan/dev/tournament-scheduler-goal-prompt.md` (mission: conversational tournament
scheduling product on top of the existing CP-SAT solver).

## Milestone checklist

- [x] **M1 — Agent skeleton**: ClaudeIntake + strict spec-mutation tools + `tourneydesk chat --brief` CLI harness + FakeIntake + fake-LLM e2e test *(merged; full gate green 2026-07-09)*
- [x] **M2 — Scoreboard**: corpus + eval runner + facts-scoped F1 scoring merged; canary F1=1.000, 0 hallucinated
- [x] **M3 — Web core**: merged, deployed (systemd + Tailscale :8445), persona-validated
- [x] **M4 — Speculative solve**: merged (10s clamp, memoized, mid-turn push), persona-validated
- [x] **M5 — Infeasibility engine**: solver instrumentation + conflict extraction + explanation/repair engine merged (web wiring of repair UI still open)
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

### Persona validation round 1 — 2026-07-09

- Fleet: 6 sonnet persona agents (first-timer, veteran, chaos, infeasibility, skeptic, mobile)
  drove the deployed site in isolated browsers. Unanimous P0: every browser joined `sessions[0]`,
  so all six shared ONE conversation — cross-contamination invalidated most rubrics.
- Real findings kept: mobile layout PASS at 390px (no h-scroll, tabs work); early intake quality
  praised (plain-language clarifying questions, explicit conflict flagging); KeyError reprs and
  error echoes leaked into chat; failed turns killed the WS loop silently; infeasibility text too
  generic (M5 lane); field_size/format ontology conflation (D15, queued).
- Fixes landed + redeployed: session-per-visit w/ URL-hash rejoin (D13), error echoes suppressed
  (D14), actionable missing-arg messages, turn-failure resilience + visible error events.
- Verified: two concurrent browsers → distinct sessions, zero cross-talk on live probe.
- Round 2 launched against the fixed deployment.

### Persona rounds 2–3 + fix batches — 2026-07-09

- **Round 2** (fresh fleet, post-isolation): P3 chaos ✓ SATISFIED, P6 mobile ✓ SATISFIED (incl.
  hash-reload rejoin). P1/P2/P4/P5 ISSUES → fix batch (D16–D19): game_format decoupled from
  field_size (P2's 8v8 was unstorable and cascaded into zero-eligible-fields solver failure),
  10s speculative clamp + honest "inconclusive" status, get_schedule_summary grounding tool,
  intake-complete gated on schedulability, error-echo suppression.
- **Round 3**: P2 veteran ✓ SATISFIED (8v8 verbatim; agent caught + collaboratively repaired a
  capacity infeasibility before closing intake). P1/P4/P5 ISSUES → fix batch (D20–D23):
  per-mutation spec push (panels update mid-turn), verified-claims prompt rule, preference
  removal tools + derived-values-need-confirmation provenance rule, memoized solve shared by
  panel and digest (CP-SAT nondeterminism made them describe different optima), game-level
  digest data. D22 queued (M6): field-spread objective.
- **Round 4 in flight**: P1/P4/P5 against the redeployed build with targeted regression scripts.
- **Orchestrator lanes delivered, READY TO MERGE (held until round 4 concludes):**
  `m2-eval-runner` — persona eval runner + facts-scoped F1 scoring; live canary scores
  b01/b03/b15 F1=1.000, hallucinated constraints 0 incl. adversarial bait; 270 tests green.
  `m5-explanation` — conflict explanation + repair-option engine (deterministic + LLM w/
  grounding guard), `just explain`; 227 tests green.

### Persona rounds 4–5 + lane merges — 2026-07-09

- **Round 4**: P1 ✓ SATISFIED (derived-values confirmation held under her exact trap; agent
  honestly reported an accepted change didn't help). P5 ✓ SATISFIED (panel/digest numbers matched
  digit-for-digit; exemplary honest change-verification — admitted an inert preference and
  removed it). P4 blocked by a real blocker: an exception escaping dispatch mid-tool-loop left a
  dangling tool_use, permanently 400-ing the session → D24 (dispatch never raises + history
  integrity finally-guard), D25 (duplicate team names rejected; ids in spec summary).
- **Round 5 (P4)**: aborted by TOTAL OUTAGE — root cause: **Anthropic API credit balance
  exhausted** (billing, not code). Honest outage messaging added for credit/auth failure classes.
- **Lanes merged to main**: m2-eval-runner (facts-scoped F1 scoring, results committed; canary
  b01/b03/b15 F1=1.000, hallucinated=0) and m5-explanation (repair engine, `just explain`).
  Resolution: main keeps the persona-validated non-strict tool suite; lane's empirical strict
  budget recorded as D26 (sentinel prototype preserved on branch).
- **Scoreboard: 5/6 personas SATISFIED** (P1 Patty, P2 Vic, P3 Carla, P5 Sam, P6 Mia).
  P4 Hank: rounds 1–4 findings all fixed and unit-verified; final live browser regression
  blocked on API credits. **Resume plan**: after top-up, re-run the round-5 Hank script
  (persona prompt archived in session; scenario in this file's round-4 entry).

### Campaign complete — 2026-07-12

- **Billing root cause fixed (D29)**: product turns were API-billed via ANTHROPIC_API_KEY
  (exported in ~/.zshrc + baked into the service env). New default provider `AgentSDKIntake`
  (Claude Agent SDK on the owner's Claude Code OAuth login) bills the Max subscription —
  verified live with a zero-credit API account. TOURNEYDESK_PROVIDER=subscription|api|fake;
  policy boundary documented (personal/dev use only; user-facing = api).
- **Round 6 (P4 Hank, subscription-billed): SATISFIED — final tally 6/6 personas satisfied.**
  Full gate: 274 passed. Goal condition met: deployed, persona-validated end to end.
- **Backlog for next iteration** (in priority order): (1) MEDIUM: message sent mid-stream is
  silently dropped — queue or reject visibly (P4 r6); (2) wire the M5 explain/repair engine
  into web chat + structured repair UI; (3) D22 field-spread solver objective; (4) stale
  source-quote refresh after corrections; (5) agent re-litigates already-stated constraints
  before resolving them (P4 r6, low); (6) M7 brackets + explanation bundle; (7) full 15-brief
  live eval sweep on the subscription provider + trend doc; (8) M8 polish + v0.2.0 tag.
