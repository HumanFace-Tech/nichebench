"""Presentation helpers for the `run` command (Rich UI pieces).

These functions centralize console output and progress bar setup so the
command logic (`run.py`) stays focused on orchestration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterator

from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table


def render_run_header(console: Console, mut_model: str, judge_model: str, profile: str | None) -> None:
    console.print(f"[cyan]Using MUT:[/cyan] {mut_model}")
    console.print(f"[cyan]Using Judge:[/cyan] {judge_model}")
    if profile:
        console.print(f"[cyan]Profile:[/cyan] {profile}")


def make_run_progress(console: Console) -> Progress:
    """Return a configured Progress instance for runs with sub-task support.

    Use as: `with make_run_progress(console) as progress:`
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,  # Keep progress visible
    )


class LiveTestRunner:
    """Live test runner that shows progress and saves results incrementally."""

    def __init__(self, console: Console, framework: str, category: str, total_tests: int, parallelism: int = 1):
        self.console = console
        self.framework = framework
        self.category = category
        self.total_tests = total_tests
        self.parallelism = parallelism
        self.completed_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0

        # Create progress for main task and worker tasks
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )

        # Main task progress
        self.main_task = None
        self.current_task = None  # For sequential mode
        self.worker_tasks: Dict[int, TaskID] = {}  # For parallel mode: worker_id -> task_id

    def __enter__(self):
        self.progress.__enter__()
        self.main_task = self.progress.add_task(
            f"[cyan]Running {self.framework}/{self.category}[/cyan] (parallelism: {self.parallelism})",
            total=self.total_tests,
        )

        # Create worker progress bars for parallel mode
        if self.parallelism > 1:
            for worker_id in range(self.parallelism):
                worker_task = self.progress.add_task(
                    f"[dim]Worker {worker_id + 1}[/dim] - Idle", total=2, visible=False
                )
                self.worker_tasks[worker_id] = worker_task

        return self

    def __exit__(self, *args):
        self.progress.__exit__(*args)

    def start_test(self, test_id: str):
        """Start processing a new test (sequential mode)."""
        if self.current_task is not None:
            self.progress.remove_task(self.current_task)

        self.current_task = self.progress.add_task(
            f"[yellow]🧪 {test_id}[/yellow] - Preparing...", total=2  # MUT, Judge
        )

    def update_test_status(self, status: str, step: int | None = None):
        """Update the current test's status."""
        if self.current_task is not None:
            if step is not None:
                self.progress.update(self.current_task, completed=step)
            self.progress.update(self.current_task, description=status)

    def update_worker_status(self, worker_id: int, test_id: str, status: str, step: int):
        """Update a specific worker's status (parallel mode)."""
        if worker_id in self.worker_tasks:
            task_id = self.worker_tasks[worker_id]
            self.progress.update(task_id, visible=True)
            self.progress.update(task_id, completed=step)
            self.progress.update(task_id, description=f"[yellow]Worker {worker_id + 1}[/yellow] - {test_id}: {status}")

    def finish_worker_test(self, worker_id: int, test_id: str, passed: bool):
        """Finish a worker's test and mark it as idle."""
        if worker_id in self.worker_tasks:
            task_id = self.worker_tasks[worker_id]
            status = "✅ Passed" if passed else "❌ Failed"
            self.progress.update(task_id, completed=2)
            self.progress.update(task_id, description=f"[dim]Worker {worker_id + 1}[/dim] - {test_id}: {status}")

            # Update counters
            if passed:
                self.passed_tests += 1
            else:
                self.failed_tests += 1

    def hide_worker(self, worker_id: int):
        """Hide a worker's progress bar when it's done."""
        if worker_id in self.worker_tasks:
            task_id = self.worker_tasks[worker_id]
            self.progress.update(task_id, visible=False)

    def advance_progress(self, amount: int = 1):
        """Advance main progress bar (for parallel execution)."""
        if self.main_task is not None:
            self.progress.advance(self.main_task, amount)

    def finish_test(self, test_id: str, passed: bool, error: str | None = None):
        """Finish the current test and update counters."""
        if self.current_task is not None:
            if error:
                status = f"[red]❌ {test_id}[/red] - Failed: {error[:30]}..."
            elif passed:
                status = f"[green]✅ {test_id}[/green] - Passed"
                self.passed_tests += 1
            else:
                status = f"[red]❌ {test_id}[/red] - Failed"
                self.failed_tests += 1

            if not error:  # Only count as completed if not an error
                self.completed_tests += 1

            self.progress.update(self.current_task, completed=2, description=status)
            if self.main_task is not None:
                self.progress.advance(self.main_task)

            # Show running totals
            main_desc = (
                f"[cyan]Running {self.framework}/{self.category}[/cyan] - "
                f"✅ {self.passed_tests} passed, ❌ {self.failed_tests} failed"
            )
            if self.main_task is not None:
                self.progress.update(self.main_task, description=main_desc)

    def show_summary(self):
        """Show final summary."""
        if self.current_task is not None:
            self.progress.remove_task(self.current_task)

        summary_desc = (
            f"[bold green]Completed {self.framework}/{self.category}[/bold green] - "
            f"✅ {self.passed_tests} passed, ❌ {self.failed_tests} failed"
        )
        if self.main_task is not None:
            self.progress.update(self.main_task, description=summary_desc)


def render_results_saved(outdir: Path, console: Console) -> None:
    console.print(f"[green]Results saved to {outdir}[/green]")


def render_live_test_result(test_id: str, passed: bool, summary: str, console: Console) -> None:
    """Render a single test result as it completes."""
    status = "[green]✅ PASS[/green]" if passed else "[red]❌ FAIL[/red]"
    console.print(f"{status} {test_id}: {summary}")


def render_incremental_summary(framework: str, category: str, passed: int, failed: int, console: Console) -> None:
    """Render an incremental summary after each test."""
    total = passed + failed
    console.print(
        f"\n[bold]Progress:[/bold] {framework}/{category} - "
        f"{total} completed (✅ {passed} passed, ❌ {failed} failed)\n"
    )
