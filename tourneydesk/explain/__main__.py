"""python -m tourneydesk.explain SPEC_YAML [--no-llm] [--json]

Standalone CLI for the conflict-explanation layer. Deliberately not wired
into `tourneydesk/cli.py` -- kept as its own `__main__`-style module so it
never touches files owned by the M3 branch.

Behavior:
  - Loads the spec and tries a normal solve first.
  - Feasible: prints "Schedule is feasible" + stats, exit 0.
  - Infeasible: extracts the conflict, prints the explanation (rich text, or
    raw JSON with --json), exit 2 -- distinguishable from a genuine CLI error
    (exit 1, e.g. the solver couldn't determine feasibility in time).
  - `--no-llm` forces the deterministic explanation path (no network).
"""

from __future__ import annotations

import json as json_module
import sys

import click
from rich.console import Console
from rich.panel import Panel

from tournament_scheduler.conflict import extract_conflict
from tournament_scheduler.pools import assign_pools
from tournament_scheduler.solver import solve
from tournament_scheduler.spec_io import load_spec
from tourneydesk.explain.engine import explain_conflict
from tourneydesk.explain.models import ConflictExplanation

console = Console()

_FEASIBLE_STATUSES = {"OPTIMAL", "FEASIBLE"}
_CONFLICT_TIME_LIMIT_S = 10.0


@click.command()
@click.argument("spec_path", type=click.Path(exists=True))
@click.option("--no-llm", is_flag=True, help="Force the deterministic explanation path (no network).")
@click.option("--json", "as_json", is_flag=True, help="Print the explanation as raw JSON instead of rich text.")
def main(spec_path: str, no_llm: bool, as_json: bool) -> None:
    """Explain why SPEC_PATH cannot be scheduled, with repair options."""
    spec = load_spec(spec_path)
    pools = assign_pools(spec)
    schedule = solve(spec, pools)

    if schedule.stats.status in _FEASIBLE_STATUSES:
        if as_json:
            click.echo(json_module.dumps({"feasible": True, "stats": schedule.stats.model_dump()}, default=str))
        else:
            console.print("[bold green]Schedule is feasible.[/bold green]")
            console.print(
                f"  {schedule.stats.num_games_scheduled} games scheduled across "
                f"{schedule.stats.num_teams} teams, {schedule.stats.num_fields} fields, "
                f"{schedule.stats.num_divisions} divisions "
                f"({schedule.stats.status}, {schedule.stats.wall_time_seconds}s)."
            )
        sys.exit(0)

    conflict = extract_conflict(spec, pools, time_limit_s=_CONFLICT_TIME_LIMIT_S)
    if conflict is None:
        # solve() reported non-feasible (e.g. UNKNOWN under a tight time
        # budget) but the instrumented extractor could not reproduce a
        # confirmed infeasibility -- ambiguous, so this is a real error, not
        # an infeasible-and-explained case.
        console.print(
            f"[yellow]Solver status was {schedule.stats.status!r} -- could not confirm infeasibility "
            "within the time budget to explain it.[/yellow]"
        )
        sys.exit(1)

    explanation = explain_conflict(spec, conflict, use_llm=False if no_llm else None)

    if as_json:
        click.echo(explanation.model_dump_json(indent=2))
    else:
        _print_explanation(explanation)
    sys.exit(2)


def _print_explanation(explanation: ConflictExplanation) -> None:
    console.print(Panel(explanation.headline, title="Why this can't be scheduled", style="bold red"))
    console.print(explanation.narrative)
    console.print()
    console.print("[bold]Repair options:[/bold]")
    for i, repair in enumerate(explanation.repairs, start=1):
        console.print(f"\n[bold cyan]{i}. {repair.title}[/bold cyan]")
        console.print(f"   {repair.description}")
        console.print(f"   [dim]Tradeoff:[/dim] {repair.tradeoff}")


if __name__ == "__main__":
    main()
