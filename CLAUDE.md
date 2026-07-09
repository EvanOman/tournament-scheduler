# TourneyDesk (tournament-scheduler)

Conversational tournament scheduling: CP-SAT solver core (`tournament_scheduler/`) +
intake agent, service layer, CLI, and web app (`tourneydesk/`). Mission spec:
`/home/evan/dev/tournament-scheduler-goal-prompt.md`. Status: `PROGRESS.md`.
Design decisions: `docs/DECISIONS.md` — log every product/engineering choice there.

## Local Deployment

Deployed as a user systemd service (`tourneydesk.service`, port 18780,
Tailscale: https://omachine.werewolf-universe.ts.net:8445/). After making code changes, run:

```bash
just redeploy
```

Secrets: `~/.config/tourneydesk/env` (ANTHROPIC_API_KEY). Frontend changes require
`cd frontend && npm run build` first (built assets are committed).

## Conventions

- `just check` must be green before any commit to main.
- CLI (`tourneydesk chat`) and web share ONE service layer (`tourneydesk/core`) — never fork logic.
- Ports: 18780+ only; never kill occupied ports. Package installs: 7-day min-release-age, never override.
- Tool schemas are non-strict by design (API compiled-grammar budget); `dispatch()` validates locally.
