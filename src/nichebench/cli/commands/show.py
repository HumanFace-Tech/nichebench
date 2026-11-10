"""Show a single test case by id."""

from pathlib import Path

import typer
from rich.console import Console

from nichebench.core.discovery import discover_frameworks

from ..rich_views.tables import render_case_panel, sort_task_specs

app = typer.Typer()
console = Console()


@app.command()
def case(test_id: str = typer.Argument(..., help="Test case id")):
    """Show the raw YAML-parsed test case for a given id."""
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    found = None
    for framework_name, tasklist in frameworks.items():
        # Scan in the canonical category order so the lookup is deterministic
        # when the same id exists in more than one category.  Deprecated
        # categories stay reachable through ``show`` even though they are
        # hidden from the listings.
        for ts in sort_task_specs(tasklist, include_deprecated=True):
            for tc in ts.testcases:
                if tc.id == test_id:
                    found = (framework_name, ts, tc)
                    break
            if found:
                break
        if found:
            break
    if not found:
        console.print(f"[red]Test case '{test_id}' not found.[/red]")
        raise typer.Exit(code=2)
    framework_name, ts, tc = found

    render_case_panel(framework_name, ts, tc)
