"""Presentation helpers for the `run` command (Rich UI pieces).

These functions centralize console output and progress bar setup so the
command logic (`run.py`) stays focused on orchestration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)


def render_run_header(console: Console, mut_model: str, judge_model: str, profile: str | None) -> None:
    console.print(f"[cyan]Using MUT:[/cyan] {mut_model}")
    console.print(f"[cyan]Using Judge:[/cyan] {judge_model}")
    if profile:
        console.print(f"[cyan]Profile:[/cyan] {profile}")


def make_run_progress(console: Console) -> Progress:
    """Return a configured Progress instance for runs.

    Use as: `with make_run_progress(console) as progress:`
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    )


def render_results_saved(outdir: Path, console: Console) -> None:
    console.print(f"[green]Results saved to {outdir}[/green]")
