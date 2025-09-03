"""
Rich report rendering for NicheBench runs.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table


def render_run_completion_report(summary_path: Path, details_path: Optional[Path] = None):
    """Render a summary report after a run (called by RUN command)."""
    console = Console()
    if not summary_path.exists():
        console.print(f"[red]No summary.json found at {summary_path}")
        return
    with open(summary_path) as f:
        summary = json.load(f)
    # Add color and emoji to summary table
    emoji_map = {
        "passed": "✅",
        "failed": "❌",
        "total": "📊",
        "framework": "🧩",
        "category": "📂",
        "model": "🤖",
    }
    # Add vertical space before summary
    console.print("\n")
    table = Table(
        title="[bold green]Run Summary[/bold green]",
        header_style="bold magenta",
        width=120,
        padding=(0, 1),
    )
    for k in summary.keys():
        col_title = f"{emoji_map.get(k, '')} {k.capitalize()}" if k in emoji_map else k.capitalize()
        table.add_column(
            col_title,
            style="cyan" if k in ("framework", "category", "model") else "white",
        )
    row = []
    for k in summary.keys():
        val = summary[k]
        if k == "passed":
            row.append(f"[bold green]{val} ✅[/bold green]")
        elif k == "failed":
            row.append(f"[bold red]{val} ❌[/bold red]")
        else:
            row.append(str(val))
    table.add_row(*row)
    console.print(table)
    # Optionally show per-test breakdown if details_path is given
    if details_path and details_path.exists():
        with open(details_path) as f:
            lines = f.readlines()
        if not lines:
            console.print("[yellow]No per-test details found.")
            return

        # Parse all lines and group by (framework, category, mut_model, judge_model)
        parsed = [json.loads(line) for line in lines]

        def group_key(row):
            return (
                row.get("framework", "?"),
                row.get("category", "?"),
                row.get("mut_model", "?"),
                row.get("judge_model", "?"),
            )

        groups = defaultdict(list)
        for row in parsed:
            # row should be a mapping/dict; guard if not
            if not isinstance(row, Mapping):
                continue
            groups[group_key(row)].append(row)

        for (fw, cat, mut, judge), rows in groups.items():
            # Consistent header formatting and width
            header_text = (
                f"[bold]Model:[/bold] [magenta]{mut}[/magenta]  |  "
                f"[bold]Judged by:[/bold] [cyan]{judge}[/cyan]  |  "
                f"[bold]Framework:[/bold] [green]{fw}[/green]  |  "
                f"[bold]Category:[/bold] [yellow]{cat}[/yellow]"
            )
            dtable = Table(show_lines=True, header_style="bold blue", padding=(0, 1))
            dtable.add_column("🧪 Test ID", style="yellow")
            dtable.add_column("Gold", style="green", justify="center")
            dtable.add_column("Judge Output", style="white", overflow="fold")
            dtable.add_column("Pass", style="bold", justify="center")
            dtable.add_column("Input", style="dim", overflow="fold")
            dtable.add_column("Output", style="dim", overflow="fold")
            for row in rows[:10]:  # Show up to 10 for now
                test_id = row.get("test_id", "?")
                gold = row.get("gold", "?")
                judge_output = row.get("judge_output", "?")
                passed = row.get("pass", False)
                inp = row.get("input", "")
                inp = (inp[:30] + "…") if len(inp) > 30 else inp
                outp = row.get("output", "")
                outp = (outp[:30] + "…") if len(outp) > 30 else outp
                pass_emoji = "[green]✅[/green]" if passed in (True, "True", 1, "1") else "[red]❌[/red]"
                dtable.add_row(str(test_id), str(gold), str(judge_output), pass_emoji, inp, outp)
            # Put the table inside the panel, let panel grow organically
            console.print("\n")
            console.print(
                Panel(
                    dtable,
                    title=header_text,
                    style="cyan",
                    padding=(1, 0, 0, 0),
                    expand=True,
                    width=140,
                )
            )


def render_run_list(run_dirs):
    """Render a table of available runs (for report list)."""
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
    runs: List[Tuple[str, str, str, str, Path]], limit: int = 10
) -> Optional[Tuple[str, str, str, str, Path]]:
    """Render an interactive run selector and return the chosen run tuple.

    Returns the chosen tuple (framework, task, model, timestamp, path) or None
    if the user quits or there are no runs.
    """
    console = Console()
    if not runs:
        console.print("[red]No runs found.")
        return None

    # Show most recent runs first, limit to `limit`
    runs_sorted = sorted(runs, key=lambda x: x[3], reverse=True)[:limit]

    table = Table(
        title="[bold cyan]Select a Run to View[/bold cyan]",
        header_style="bold magenta",
        width=120,
        padding=(0, 1),
    )
    table.add_column("#", style="bold yellow", width=5, justify="center")
    table.add_column("🧩 Framework", style="cyan", width=15)
    table.add_column("📂 Task", style="cyan", width=15)
    table.add_column("🤖 Model", style="magenta", width=20)
    table.add_column("⏰ Timestamp", style="yellow", width=20)

    for idx, (fw, task, model, ts, path) in enumerate(runs_sorted, 1):
        table.add_row(str(idx), fw, task, model, ts)

    console.print(table)
    choice = Prompt.ask(
        "Select a run to view (1-{}), or [b]q[/b] to quit".format(len(runs_sorted)),
        choices=[str(i) for i in range(1, len(runs_sorted) + 1)] + ["q"],
        default="1",
    )
    if choice == "q":
        return None
    idx = int(choice) - 1
    return runs_sorted[idx]
