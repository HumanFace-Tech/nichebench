"""Typer app wiring and top-level commands."""

import sys

import typer

from .commands import list as list_cmd
from .commands import report as report_cmd
from .commands import run as run_cmd
from .commands import show as show_cmd

app = typer.Typer(help="NicheBench: Framework-specific LLM evals with rich CLI, LLM-as-a-Judge, and auto-discovery.")

app.command(name="list", help="List available frameworks and tasks.")(list_cmd.frameworks)
app.command(name="list-tasks", help="List tasks for a given framework.")(list_cmd.tasks)
app.command(name="show", help="Show a test case by id.")(show_cmd.case)
app.command(name="run", help="Run all test cases for a framework/category.")(run_cmd.all)
app.add_typer(
    report_cmd.app,
    name="report",
    help="Reporting: list and show past runs.",
    invoke_without_command=True,
)


def main():
    # If no arguments provided, show help and exit cleanly
    if len(sys.argv) == 1:
        app(["--help"])
        return

    app()


if __name__ == "__main__":
    main()
