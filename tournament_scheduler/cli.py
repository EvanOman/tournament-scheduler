"""CLI entry point: tournament-scheduler solve spec.yaml"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from tournament_scheduler.pools import assign_pools
from tournament_scheduler.renderer import render_html, render_markdown
from tournament_scheduler.solver import solve
from tournament_scheduler.spec_io import load_spec

console = Console()


@click.group()
@click.version_option(package_name="tournament-scheduler")
def main() -> None:
    """Tournament Scheduler: OR-Tools CP-SAT engine for youth sports tournaments."""


@main.command()
@click.argument("spec_file", type=click.Path(exists=True))
@click.option("--format", "output_format", type=click.Choice(["markdown", "html"]), default="markdown")
@click.option("--output", "-o", "output_path", type=click.Path(), default=None, help="Output file path")
@click.option("--validate/--no-validate", default=True, help="Run validation after solving")
def solve_cmd(spec_file: str, output_format: str, output_path: str | None, validate: bool) -> None:
    """Solve a tournament schedule from a YAML/JSON spec file."""
    console.print(f"[bold]Loading spec:[/bold] {spec_file}")

    try:
        spec = load_spec(spec_file)
    except Exception as e:
        console.print(f"[red]Error loading spec:[/red] {e}")
        raise SystemExit(1) from e

    console.print(f"[bold]Tournament:[/bold] {spec.name}")
    console.print(f"  {len(spec.teams)} teams, {len(spec.divisions)} divisions, {len(spec.fields)} fields")

    # Phase 0: Pool assignment
    console.print("[bold]Assigning pools...[/bold]")
    pools = assign_pools(spec)
    console.print(f"  Created {len(pools)} pools")

    # Phase 1: Solve
    console.print(f"[bold]Solving[/bold] (max {spec.max_solve_seconds}s, {spec.num_workers} workers)...")
    schedule = solve(spec, pools)

    # Report solver stats
    stats = schedule.stats
    status_color = "green" if stats.status in ("OPTIMAL", "FEASIBLE") else "red"
    console.print(
        Panel(
            f"Status: [{status_color}]{stats.status}[/{status_color}]\n"
            f"Wall time: {stats.wall_time_seconds:.2f}s\n"
            f"Games scheduled: {stats.num_games_scheduled}\n"
            f"Objective: {stats.objective_value}\n"
            f"Conflicts: {stats.num_conflicts}\n"
            f"Branches: {stats.num_branches}",
            title="Solver Results",
        )
    )

    if stats.status not in ("OPTIMAL", "FEASIBLE"):
        console.print("[red]No feasible solution found. Check constraints for conflicts.[/red]")
        raise SystemExit(1)

    # Validate
    if validate:
        console.print("[bold]Validating schedule...[/bold]")
        from tournament_scheduler.validator import validate as validate_fn

        result = validate_fn(schedule, spec)
        if result.valid:
            console.print("[green]Schedule is valid.[/green]")
        else:
            console.print(f"[red]{result.summary()}[/red]")

        for w in result.warnings:
            console.print(f"[yellow]  WARNING: {w}[/yellow]")

    # Render
    if output_format == "markdown":
        rendered = render_markdown(schedule, spec)
    else:
        rendered = render_html(schedule, spec)

    if output_path:
        out = Path(output_path)
    else:
        suffix = ".md" if output_format == "markdown" else ".html"
        out = Path(spec_file).stem + f"_schedule{suffix}"  # type: ignore[assignment]
        out = Path(out)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered)
    console.print(f"[bold]Output written to:[/bold] {out}")


@main.command()
@click.argument("spec_file", type=click.Path(exists=True))
def validate_cmd(spec_file: str) -> None:
    """Validate a tournament spec file without solving."""
    try:
        spec = load_spec(spec_file)
        console.print(f"[green]Spec is valid:[/green] {spec.name}")
        console.print(f"  {len(spec.teams)} teams, {len(spec.divisions)} divisions, {len(spec.fields)} fields")
    except Exception as e:
        console.print(f"[red]Spec validation failed:[/red] {e}")
        raise SystemExit(1) from e


@main.command()
@click.option("--output-dir", "-o", default="examples", help="Output directory for fixtures")
def generate_fixtures(output_dir: str) -> None:
    """Generate example tournament fixture files."""
    from tournament_scheduler.fixtures import generate_all

    generate_all(output_dir)
    console.print(f"[green]Fixtures generated in {output_dir}/[/green]")


if __name__ == "__main__":
    main()
