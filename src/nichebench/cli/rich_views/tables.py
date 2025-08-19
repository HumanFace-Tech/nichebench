"""Reusable rich table components for nichebench CLI."""
from rich.table import Table


def make_frameworks_table():
    table = Table(title="Frameworks")
    table.add_column("framework")
    table.add_column("task_types")
    table.add_column("total_tests", justify="right")
    return table


def render_report_table(summary: dict, title: str = None):
    from rich.console import Console

    table = Table(title=title or "Results Summary")
    for k in summary.keys():
        table.add_column(str(k))
    table.add_row(*[str(summary[k]) for k in summary.keys()])
    console = Console()
    console.print(table)
