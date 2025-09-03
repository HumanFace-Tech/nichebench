"""
CLI command: report
- Loads results JSON (from results/<framework>/<task>/<model>/<timestamp>/summary.json)
- Optionally uploads to Confident AI (deepeval cloud) if --confident-ai is set
- Always prints a Rich table summary
"""

from pathlib import Path

import typer

from ..rich_views.reports import (
    render_run_completion_report,
    render_run_list,
    render_run_selector,
)
from .report_utils import find_all_run_dirs


def _interactive_report_selector():
    runs = list(find_all_run_dirs())
    selected = render_run_selector(runs)
    if not selected:
        raise typer.Exit(1)
    fw, task, model, ts, path = selected
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
