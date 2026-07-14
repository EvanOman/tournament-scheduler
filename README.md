# Tournament Scheduler

A research implementation of constraint-based tournament scheduling with Google OR-Tools CP-SAT, a conversational intake layer, and a small web interface.

The core scheduler reads a declarative tournament spec, assigns teams to pools, solves the pool-play schedule, validates the result independently, and renders the schedule for review. The companion `tourneydesk` package explores the workflow around the solver: conversational intake, rule extraction, infeasibility explanation, and repair suggestions.

The project is alpha-quality and built as an engineering prototype. Pool-play scheduling is implemented; bracket scheduling and full-tournament re-optimization are still future work.

## What It Does

- Solves pool-play tournament schedules with OR-Tools CP-SAT.
- Represents tournament inputs as typed YAML/JSON specs.
- Supports teams, divisions, field sizes, field availability windows, game durations, changeover buffers, minimum rest, coaching conflicts, pool structure, and weighted time/field preferences.
- Validates generated schedules with an independent checker rather than relying only on solver feasibility.
- Renders schedules as Markdown, standalone HTML, and web-app payloads.
- Explains infeasible specs with a deterministic conflict extractor and an optional LLM explanation layer.
- Includes a conversational intake service that turns tournament facts into structured scheduling rules.
- Includes an eval corpus for testing intake behavior against synthetic briefs and golden specs.

## Architecture

```text
Tournament brief or YAML/JSON spec
        |
        v
Conversational intake / spec loader
        |
        v
TournamentSpec
        |
        v
Pool assignment
        |
        v
CP-SAT solver
        |
        v
Independent validator
        |
        v
Renderer / web API / conflict explanation
```

The solver uses a decomposition pipeline:

1. Pool assignment uses serpentine seeding and does not require the constraint solver.
2. Pool-play scheduling maps round-robin games onto field/time slots with CP-SAT.
3. Validation re-checks hard constraints against the produced schedule.
4. Rendering and explanation code turn solver output into human-readable artifacts.

## Components

- `tournament_scheduler/`: core models, spec loading, pool assignment, CP-SAT solver, validation, rendering, and conflict extraction.
- `tourneydesk/`: conversational intake service, provider adapters, web app backend, session state, and explanation engine.
- `frontend/`: TypeScript/Vite single-page app for chat, rules, and schedule review.
- `evals/`: synthetic intake briefs, golden specs, scoring, and runner.
- `tests/`: unit and integration tests for solver behavior, validation, rendering, web flows, conflict explanation, and eval tooling.
- `examples/`: generated tournament specs at small, medium, and large sizes.

## Tech Stack

- Python 3.12+
- OR-Tools CP-SAT
- Pydantic
- FastAPI and Uvicorn
- TypeScript and Vite
- pytest, ruff, and ty
- uv and just

## Quick Start

```bash
uv sync --dev

uv run tournament-scheduler generate-fixtures
uv run tournament-scheduler solve examples/small_tournament.yaml
uv run tournament-scheduler solve examples/small_tournament.yaml --format html

just test
```

The solver writes schedule output next to the input file unless an explicit `--output` path is provided.

## Web App

Run the local web app with the offline provider:

```bash
just serve --provider fake
```

By default, the server binds to `127.0.0.1:18780` and automatically chooses the next open port if that port is busy. The app serves the SPA, REST endpoints, and WebSocket session flow from the same FastAPI process.

For provider-backed intake, set the relevant provider configuration and run:

```bash
just serve --provider api
```

## Evals

Run the full synthetic intake corpus:

```bash
just eval --provider fake
```

Run a subset by ID:

```bash
just eval --provider fake --ids b01_clean_small
```

The eval runner compares generated specs against golden specs and records structured scoring output.

## Development

```bash
just check        # lint, format check, typecheck, tests, frontend typecheck/build
just fix          # ruff autofix + format
just fc           # fix, then check
just test         # Python tests
just frontend-check
```

The frontend can also be checked directly:

```bash
cd frontend
npm install
npm run typecheck
npm run build
```

## Example Fixtures

| Fixture | Teams | Divisions | Fields | Format | Expected games |
| --- | ---: | ---: | ---: | --- | ---: |
| Small | 24 | 3 | 4 | Single day | 36 |
| Medium | 48 | 5 | 6 | Weekend | 72 |
| Large | 96 | 8 | 10 | Weekend | 144 |

## Current Status

Implemented:

- Pool-play scheduling for synthetic tournament specs.
- Hard-constraint validation after solve.
- Markdown, HTML, and web schedule rendering.
- Conversational rule intake with fake and provider-backed adapters.
- Infeasibility extraction and explanation.
- Eval fixtures for common intake cases, missing information, contradictory constraints, and adversarial prompts.

Not implemented yet:

- Bracket scheduling after pool play.
- Joint re-optimization across pool and bracket phases.
- Import/export adapters for external tournament systems.
