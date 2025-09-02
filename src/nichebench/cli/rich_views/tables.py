"""Reusable rich table components for nichebench CLI."""

from typing import Dict, Iterable, Mapping, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def render_frameworks_table(frameworks: Mapping[str, Iterable]):
    """Render the frameworks by category table (used by `nichebench list frameworks`)."""
    console = Console()
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
        # tasklist is an iterable of TaskSpec objects
        cat_counts = {t.task_type: len(t.testcases) for t in tasklist}
        total = sum(cat_counts.values())
        for cat, count in cat_counts.items():
            table.add_row(
                f"[yellow]{name}[/yellow]",
                f"[green]{cat}[/green]",
                f"[bold blue]{count}[/bold blue]",
                f"[white]{total}[/white]",
            )
    console.print(table)


def render_frameworks_overview_table(frameworks: Mapping[str, Iterable]):
    """Render the discovered frameworks overview table (used by `nichebench list tasks` with no framework)."""
    console = Console()
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


def render_tasks_for_framework(framework: str, tasklist: Iterable):
    """Render the task listing table for a single framework."""
    console = Console()
    table = Table(
        title=f"[bold cyan]Tasks in {framework}[/bold cyan]",
        box=box.SIMPLE_HEAVY,
        border_style="cyan",
    )
    table.add_column("[bold green]Category[/bold green]", style="green")
    table.add_column("[bold yellow]Test ID[/bold yellow]", style="yellow")
    table.add_column("[bold white]Summary[/bold white]", style="white")
    for ts in tasklist:
        for tc in ts.testcases:
            table.add_row(
                f"[green]{ts.task_type}[/green]",
                f"[yellow]{tc.id}[/yellow]",
                (tc.summary or "")[:80],
            )
    console.print(table)


def render_case_panel(framework_name: str, ts, tc) -> None:
    """Render a nicely formatted panel for a single test case (used by `show case`)."""
    console = Console()
    body_lines = [
        f"Framework: {framework_name}",
        f"Type: {ts.task_type}",
        f"ID: {tc.id}",
        f"Summary: {tc.summary or ''}",
        "",
        "Context:",
        (tc.context or "")[:1000],
        "",
        "Raw:",
        str(tc.raw),
    ]
    body = "\n".join(body_lines)
    console.print(Panel(body, title=f"{tc.id}"))


def make_frameworks_table():
    table = Table(title="Frameworks")
    table.add_column("framework")
    table.add_column("task_types")
    table.add_column("total_tests", justify="right")
    return table


def render_report_table(summary: dict, title: Optional[str] = None) -> None:
    from rich.console import Console

    table = Table(title=title or "Results Summary")
    for k in summary.keys():
        table.add_column(str(k))
    table.add_row(*[str(summary[k]) for k in summary.keys()])
    console = Console()
    console.print(table)
