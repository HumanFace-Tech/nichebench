"""CLI command: forensics — analyse trial and run artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from nichebench.execution.diagnostics import collect_reports, format_text_report

app = typer.Typer()


@app.command()
def forensics(
    path: Path = typer.Option(..., "--path", help="Trial directory or run directory to analyse."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON array instead of human text."),
) -> None:
    """Analyse NicheBench trial/run artifacts and produce a forensics report."""
    resolved = path.resolve()
    if not resolved.exists():
        typer.echo(f"[forensics] ERROR: Path does not exist: {resolved}", err=True)
        raise typer.Exit(code=1)

    reports = collect_reports(resolved)
    if not reports:
        typer.echo("[forensics] No trials found at the given path.", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(reports, indent=2, default=str))
    else:
        typer.echo(format_text_report(reports))
