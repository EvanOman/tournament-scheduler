# Tournament Scheduler

An OR-Tools CP-SAT scheduling engine for youth sports tournaments, built to prove the core thesis of a concierge scheduling service.

## The Problem

Tournament directors spend 14-60+ hours manually building schedules for youth soccer tournaments. Existing software (GotSport, SportsEngine Tourney) provides drag-and-drop grids but no real constraint optimization. The professional-grade solvers (GotSport Pro with Gurobi) are locked behind enterprise paywalls. Directors are left hand-placing games while juggling dozens of overlapping constraints.

## The Thesis

A concierge scheduling service at $200-500/tournament for Tier 2 regional soccer (40-150 teams) is viable. The technical core is Google OR-Tools CP-SAT -- a free, open-source constraint solver that won all CP competition gold medals for five consecutive years. The AI opportunity is not in the solver (commodity) but in the surrounding workflow: LLM-powered intake (natural language to formal constraints) and schedule explanation.

This repository is the MVP solver engine. It takes a declarative YAML spec describing a tournament and produces a valid, optimized schedule in seconds.

## What It Does

- **Constraint solver** using Google OR-Tools CP-SAT, handling the real constraints from youth soccer:
  - Teams/divisions/age groups with field size requirements
  - Field availability windows (per-field, per-day)
  - Game durations + halftime + changeover buffers
  - Minimum rest between games for player safety
  - No team plays twice simultaneously
  - Coach-coaching-multiple-teams conflicts
  - Pool-play structure (serpentine seeding, round-robin within pools)
  - Time and field preferences as weighted soft constraints
  - Early/late game balance across teams

- **Declarative spec** (YAML/JSON) designed as a clean target for an LLM intake layer

- **Independent validation** that programmatically checks every hard constraint

- **Human-readable output** as markdown tables and standalone HTML pages with per-field timelines and per-team itineraries

## Quick Start

```bash
# Install
uv sync --dev

# Generate example tournament fixtures
uv run tournament-scheduler generate-fixtures

# Solve a tournament
uv run tournament-scheduler solve examples/small_tournament.yaml

# Solve with HTML output
uv run tournament-scheduler solve examples/small_tournament.yaml --format html

# Run tests
just test
```

## Architecture

```
TournamentSpec (YAML/JSON)
    |
    v
Pool Assignment (serpentine seeding)
    |
    v
CP-SAT Solver (hard + soft constraints)
    |
    v
Validation (independent constraint checking)
    |
    v
Renderer (markdown / HTML)
```

The solver follows a decomposition pipeline recommended by the OR literature:
1. **Phase 0 -- Pool Assignment**: Assign teams to pools using serpentine seeding (simple heuristic, no solver needed)
2. **Phase 1 -- Pool Play Scheduling**: Schedule all round-robin games within pools onto fields and time slots (CP-SAT)
3. **Phase 2 -- Bracket Scheduling**: (Not yet implemented) Schedule elimination bracket games
4. **Phase 3 -- Global Optimization**: (Not yet implemented) Re-optimize the full schedule jointly

## Test Fixtures

Three realistic synthetic tournaments at increasing scale:

| Fixture | Teams | Divisions | Fields | Format | Expected Games |
|---------|-------|-----------|--------|--------|---------------|
| Small   | 24    | 3         | 4      | Single day (Sat) | 36 |
| Medium  | 48    | 5         | 6      | Weekend (Sat+Sun) | 72 |
| Large   | 96    | 8         | 10     | Weekend (Sat+Sun) | 144 |

## What's Proven

- CP-SAT solves realistic youth tournament instances (24-96 teams) with full constraint satisfaction
- All hard constraints are verified by an independent validator
- Schedule quality is measurable via the weighted objective function
- The declarative spec format is clean enough for LLM intake targeting

## What's Next

1. **Design partner validation**: Take the solver output to the design partner (an experienced tournament director) and compare against their manually-built schedules
2. **LLM intake layer**: Wire up the `IntakeProvider` interface to parse natural language tournament descriptions into the spec format
3. **Bracket scheduling**: Add single-elimination bracket support after pool play
4. **Schedule explanation**: Generate natural-language explanations of scheduling decisions
5. **Incremental re-scheduling**: Handle real-time changes (team drops, weather delays) without full re-solve
6. **GotSport export**: Output in a format importable by GotSport

## Research Foundation

This project is grounded in 9 deep-research reports covering market analysis, competitive landscape, technical approach, and go-to-market strategy. Key references:

- **Solver**: Google OR-Tools CP-SAT (Apache 2.0, gold medalist in CP competitions for 5 years)
- **Problem class**: NP-hard in general form, but practical instances (16-200 teams) are well within CP-SAT capability
- **Architecture**: Decomposition pipeline (pool assignment -> pool scheduling -> bracket scheduling -> global optimization)
- **Performance targets**: <5s for 16 teams, <30s for 48 teams, <5min for 128 teams

## Development

```bash
just check    # lint + format + typecheck + test
just fix      # auto-fix lint/format issues
just fc       # fix then check
```
