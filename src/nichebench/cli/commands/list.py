"""List frameworks and tasks available."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from nichebench.core.discovery import discover_frameworks

from ..rich_views.tables import (
    render_frameworks_overview_table,
    render_frameworks_table,
    render_tasks_for_framework,
)

app = typer.Typer()
console = Console()


def _category_counts(tasklist):
    # Returns dict: {task_type: count}
    return {t.task_type: len(t.testcases) for t in tasklist}


@app.command()
def frameworks():
    """List discovered frameworks, one row per test category."""
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    render_frameworks_table(frameworks)


@app.command()
def tasks(framework: Optional[str] = typer.Argument(None, help="framework name (optional)")):
    """List tasks for a given framework.

    If `framework` is omitted, show the list of discovered frameworks instead of erroring.
    """
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    # If no framework specified, print available frameworks and counts.
    if framework is None:
        render_frameworks_overview_table(frameworks)
        return
    if framework not in frameworks:
        console.print(f"[red]Framework '{framework}' not found.[/red]")
        raise typer.Exit(code=1)
    render_tasks_for_framework(framework, frameworks[framework])
