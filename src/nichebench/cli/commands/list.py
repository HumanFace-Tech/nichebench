"""List frameworks and tasks available."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from nichebench.core.discovery import discover_frameworks

from ..rich_views.tables import (
    render_frameworks_overview_table,
    render_frameworks_table,
    render_tasks_for_framework,
)

app = typer.Typer()
console = Console()


@app.command()
def frameworks(
    include_all: bool = typer.Option(
        False,
        "--all",
        help="Include removed, non-runnable categories (e.g. code_agent) for historical discovery.",
    ),
):
    """List discovered frameworks, one row per test category."""
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    render_frameworks_table(frameworks, include_deprecated=include_all)


@app.command()
def tasks(
    framework: Optional[str] = typer.Argument(None, help="framework name (optional)"),
    include_all: bool = typer.Option(
        False,
        "--all",
        help="Include removed, non-runnable categories (e.g. code_agent) for historical discovery.",
    ),
):
    """List tasks for a given framework.

    If `framework` is omitted, show the list of discovered frameworks instead of erroring.
    """
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    # If no framework specified, print available frameworks and counts.
    if framework is None:
        render_frameworks_overview_table(frameworks, include_deprecated=include_all)
        return
    if framework not in frameworks:
        console.print(f"[red]Framework '{framework}' not found.[/red]")
        raise typer.Exit(code=1)
    render_tasks_for_framework(framework, frameworks[framework], include_deprecated=include_all)
