"""List frameworks and tasks available."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from nichebench.core.discovery import discover_frameworks

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
    from rich import box
    from rich.panel import Panel
    from rich.text import Text

    splash = Text("NicheBench", style="bold magenta", justify="center")
    splash.append("  —  Framework Packs", style="dim")
    console.print(Panel(splash, style="bold magenta", expand=False, border_style="magenta"))

    table = Table(
        title="[bold cyan]Test Categories by Framework[/bold cyan]",
        box=box.ROUNDED,
        border_style="cyan",
    )
    table.add_column("[bold yellow]Framework[/bold yellow]", style="bold yellow", no_wrap=True)
    table.add_column("[bold green]Category[/bold green]", style="green", no_wrap=True)
    table.add_column("[bold blue]Test Count[/bold blue]", style="bold blue", justify="right")
    table.add_column("[bold white]Framework Total[/bold white]", style="bold white", justify="right")

    for name, tasklist in frameworks.items():
        cat_counts = _category_counts(tasklist)
        total = sum(cat_counts.values())
        for cat, count in cat_counts.items():
            table.add_row(
                f"[yellow]{name}[/yellow]",
                f"[green]{cat}[/green]",
                f"[bold blue]{count}[/bold blue]",
                f"[white]{total}[/white]",
            )
    console.print(table)


@app.command()
def tasks(framework: Optional[str] = typer.Argument(None, help="framework name (optional)")):
    """List tasks for a given framework.

    If `framework` is omitted, show the list of discovered frameworks instead of erroring.
    """
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    # If no framework specified, print available frameworks and counts.
    if framework is None:
        from rich import box

        table = Table(
            title="[bold cyan]Discovered Frameworks[/bold cyan]",
            box=box.SIMPLE,
            border_style="cyan",
        )
        table.add_column("[bold yellow]Framework[/bold yellow]", style="bold yellow")
        table.add_column("[bold blue]Total Tasks[/bold blue]", style="bold blue", justify="right")
        for name, tasklist in frameworks.items():
            total = sum(len(t.testcases) for t in tasklist)
            table.add_row(f"[yellow]{name}[/yellow]", f"[bold blue]{total}[/bold blue]")
        console.print(table)
        return
    if framework not in frameworks:
        console.print(f"[red]Framework '{framework}' not found.[/red]")
        raise typer.Exit(code=1)
    from rich import box

    table = Table(
        title=f"[bold cyan]Tasks in {framework}[/bold cyan]",
        box=box.SIMPLE_HEAVY,
        border_style="cyan",
    )
    table.add_column("[bold green]Category[/bold green]", style="green")
    table.add_column("[bold yellow]Test ID[/bold yellow]", style="yellow")
    table.add_column("[bold white]Summary[/bold white]", style="white")
    for ts in frameworks[framework]:
        for tc in ts.testcases:
            table.add_row(
                f"[green]{ts.task_type}[/green]",
                f"[yellow]{tc.id}[/yellow]",
                (tc.summary or "")[:80],
            )
    console.print(table)
