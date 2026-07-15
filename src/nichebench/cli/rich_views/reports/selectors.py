"""Interactive run selector and run listing.

Owner: ``render_run_selector`` and ``render_run_list``.

``render_run_list`` prints a table of all available run directories.
``render_run_selector`` adds an interactive paginated picker that returns
the chosen run path as a ``(framework, task, model, timestamp, path)`` tuple.
"""

from pathlib import Path
from typing import List, Optional, Tuple

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table


def render_run_list(run_dirs):
    """Print a table of all available run directories.

    Args:
        run_dirs: iterable of ``(framework, task, model, timestamp, path)``
          tuples, typically loaded from the results directory listing.
    """
    console = Console()
    table = Table(
        title="[bold cyan]Available Runs[/bold cyan]",
        header_style="bold magenta",
        padding=(0, 1),
    )
    table.add_column("🧩 Framework", style="cyan")
    table.add_column("📂 Task", style="cyan")
    table.add_column("🤖 Model", style="magenta")
    table.add_column("⏰ Timestamp", style="yellow")
    table.add_column("📁 Path", style="white", overflow="fold")
    for fw, task, model, ts, path in run_dirs:
        table.add_row(fw, task, model, ts, str(path))
    console.print(table)


def render_run_selector(
    runs: List[Tuple[str, str, str, str, Path]], limit: int = 20
) -> Optional[Tuple[str, str, str, str, Path]]:
    """Render an interactive run selector with pagination and return the chosen run tuple.

    Returns the chosen tuple (framework, task, model, timestamp, path) or None
    if the user quits or there are no runs.
    """
    console = Console()
    if not runs:
        console.print("[red]No runs found.")
        return None

    # Show most recent runs first
    runs_sorted = sorted(runs, key=lambda x: x[3], reverse=True)
    total_runs = len(runs_sorted)
    page = 0
    page_size = limit

    while True:
        # Calculate pagination
        start_idx = page * page_size
        end_idx = min(start_idx + page_size, total_runs)
        current_runs = runs_sorted[start_idx:end_idx]
        total_pages = (total_runs + page_size - 1) // page_size

        # Clear screen and show table
        console.clear()

        table = Table(
            title=(
                f"[bold cyan]Select a Run to View[/bold cyan] "
                f"(Page {page + 1}/{total_pages}, {total_runs} total runs)"
            ),
            header_style="bold magenta",
            width=120,
            padding=(0, 1),
        )
        table.add_column("#", style="bold yellow", width=5, justify="center")
        table.add_column("🧩 Framework", style="cyan", width=15)
        table.add_column("📂 Task", style="cyan", width=15)
        table.add_column("🤖 Model", style="magenta", width=20)
        table.add_column("⏰ Timestamp", style="yellow", width=20)

        for local_idx, (fw, task, model, ts, path) in enumerate(current_runs, 1):
            global_idx = start_idx + local_idx
            table.add_row(str(global_idx), fw, task, model, ts)

        console.print(table)

        # Build choices and prompt
        choices = [str(i) for i in range(start_idx + 1, end_idx + 1)] + ["q"]
        prompt_parts = [f"Select a run (1-{total_runs})"]

        if total_pages > 1:
            nav_options = []
            if page > 0:
                choices.append("p")
                nav_options.append("[b]p[/b]revious page")
            if page < total_pages - 1:
                choices.append("n")
                nav_options.append("[b]n[/b]ext page")
            if nav_options:
                prompt_parts.append(", ".join(nav_options))

        prompt_parts.append("[b]q[/b]uit")
        prompt_text = f"{', '.join(prompt_parts)}"

        choice = Prompt.ask(prompt_text, choices=choices, default="1")

        if choice == "q":
            return None
        if choice == "p" and page > 0:
            page -= 1
            continue
        if choice == "n" and page < total_pages - 1:
            page += 1
            continue
        # User selected a run number
        selected_idx = int(choice) - 1
        return runs_sorted[selected_idx]
