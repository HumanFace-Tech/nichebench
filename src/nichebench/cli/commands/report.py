"""
CLI command: report
- Loads results JSON (from results/<framework>/<task>/<model>/<timestamp>/summary.json)
- Optionally uploads to Confident AI (deepeval cloud) if --confident-ai is set
- Always prints a Rich table summary
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from ..rich_views.reports import render_run_completion_report, render_run_list
from .report_utils import find_all_run_dirs


def _interactive_report_selector():
    runs = list(find_all_run_dirs())
    if not runs:
        Console().print("[red]No runs found.")
        raise typer.Exit(1)
    # Show last 10 runs (most recent last)
    runs = sorted(runs, key=lambda x: x[3], reverse=True)[:10]
    table = Table(
        title="[bold cyan]Select a Run to View[/bold cyan]",
        header_style="bold magenta",
        width=120,
        padding=(0, 1),
    )
    table.add_column("#", style="bold yellow", width=5, justify="center")
    table.add_column("🧩 Framework", style="cyan", width=15)
    table.add_column("📂 Task", style="cyan", width=15)
    table.add_column("🤖 Model", style="magenta", width=20)
    table.add_column("⏰ Timestamp", style="yellow", width=20)
    for idx, (fw, task, model, ts, path) in enumerate(runs, 1):
        table.add_row(str(idx), fw, task, model, ts)
    console = Console()
    console.print(table)
    choice = Prompt.ask(
        "Select a run to view (1-{}), or [b]q[/b] to quit".format(len(runs)),
        choices=[str(i) for i in range(1, len(runs) + 1)] + ["q"],
        default="1",
    )
    if choice == "q":
        raise typer.Exit(0)
    idx = int(choice) - 1
    fw, task, model, ts, path = runs[idx]
    summary_path = path / "summary.json"
    details_path = path / "details.jsonl"
    render_run_completion_report(summary_path, details_path)


app = typer.Typer()


@app.callback()
def main(ctx: typer.Context):
    """Reporting: list and show past runs. If no subcommand, show interactive selector."""
    if ctx.invoked_subcommand is None:
        _interactive_report_selector()


@app.command("list")
def report_list():
    """List all available runs (framework/task/model/timestamp)."""
    runs = list(find_all_run_dirs())
    render_run_list(runs)


@app.command("show")
def report_show(
    framework: str = typer.Argument(..., help="Framework name (e.g. drupal)"),
    task: str = typer.Argument(..., help="Task type (e.g. quiz, code_generation, bug_fixing)"),
    model: str = typer.Argument(..., help="Model name (e.g. gpt-4, dummy-model)"),
    timestamp: str = typer.Option(None, help="Timestamp dir (default: latest)"),
):
    """Show a summary report for a specific run."""
    results_root = Path("results")
    base = results_root / framework / task / model
    if not base.exists():
        typer.echo(f"No results found for {framework}/{task}/{model}")
        raise typer.Exit(1)
    if timestamp:
        results_dir = base / timestamp
    else:
        subdirs = [d for d in base.iterdir() if d.is_dir()]
        if not subdirs:
            typer.echo(f"No result runs found in {base}")
            raise typer.Exit(1)
        results_dir = max(subdirs, key=lambda d: d.name)
    summary_path = results_dir / "summary.json"
    details_path = results_dir / "details.jsonl"
    render_run_completion_report(summary_path, details_path)
