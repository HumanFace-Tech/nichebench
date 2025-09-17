"""
Rich report rendering for NicheBench runs.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

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

    # Clean summary table - exclude config and other verbose data
    display_keys = ["framework", "category", "model", "judge", "total", "results", "avg_score"]
    emoji_map = {
        "results": "📊",
        "avg_score": "🎯",
        "total": "📊",
        "framework": "🧩",
        "category": "📂",
        "model": "🤖",
        "judge": "⚖️",
    }

    # Add vertical space before summary
    console.print("\n")
    table = Table(
        title="[bold green]Run Summary[/bold green]",
        header_style="bold magenta",
        width=160,
        padding=(0, 1),
    )

    for key in display_keys:
        col_title = (
            f"{emoji_map.get(key, '')} {key.replace('_', ' ').title()}"
            if key in emoji_map
            else key.replace("_", " ").title()
        )
        table.add_column(
            col_title,
            style="cyan" if key in ("framework", "category", "model", "judge") else "white",
        )

    table_row = []
    for key in display_keys:
        if key == "results":
            # Always construct the results column from passed/partial/failed data
            passed = summary.get("passed", 0)
            partial = summary.get("partial", 0)
            failed = summary.get("failed", 0)

            results_parts = []
            if passed > 0:
                results_parts.append(f"[bold green]✅ {passed}[/bold green]")
            if partial > 0:
                results_parts.append(f"[bold yellow]🟡 {partial}[/bold yellow]")
            if failed > 0:
                results_parts.append(f"[bold red]❌ {failed}[/bold red]")

            table_row.append("\n".join(results_parts) if results_parts else "0")
        elif key == "avg_score":
            # Always try to show average score
            avg_score = summary.get("avg_score", 0.0)
            score_percent = int(avg_score * 100)
            if score_percent > 66:
                table_row.append(f"[bold green]{score_percent}%[/bold green]")
            elif score_percent >= 33:
                table_row.append(f"[bold yellow]{score_percent}%[/bold yellow]")
            else:
                table_row.append(f"[bold red]{score_percent}%[/bold red]")
        elif key in summary:
            val = summary[key]
            table_row.append(str(val))
        else:
            table_row.append("N/A")
    table.add_row(*table_row)
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
            # rows are already Mapping objects from the filtering above

            # Count failed tests for this group
            failed_count = 0
            for row in rows:
                if cat in ("code_generation", "bug_fixing", "code_agent"):
                    judge_output = row.get("judge_output", {})
                    if isinstance(judge_output, dict):
                        score = judge_output.get("overall_score", 0.0)
                        if score <= 0.66:
                            failed_count += 1
                    else:
                        if not row.get("pass", False):
                            failed_count += 1
                else:
                    if not row.get("pass", False):
                        failed_count += 1

            # Skip if no failures to show
            if failed_count == 0:
                continue  # Consistent header formatting and width with failure count
            header_text = (
                f"[bold]Model:[/bold] [magenta]{mut}[/magenta]  |  "
                f"[bold]Judged by:[/bold] [cyan]{judge}[/cyan]  |  "
                f"[bold]Framework:[/bold] [green]{fw}[/green]  |  "
                f"[bold]Category:[/bold] [yellow]{cat}[/yellow]  |  "
                f"[bold red]❌ Failed Tests: {failed_count}[/bold red]"
            )

            # Create different table layouts based on category
            if cat == "quiz":
                _render_quiz_details_table(console, header_text, rows)
            elif cat in ("code_generation", "bug_fixing", "code_agent"):
                _render_code_details_table(console, header_text, rows, cat)
            else:
                # Fallback for unknown categories
                _render_generic_details_table(console, header_text, rows)


def _render_quiz_details_table(console: Console, header_text: str, rows: List[Mapping]):
    """Render details table for quiz category."""
    dtable = Table(show_lines=True, header_style="bold blue", padding=(0, 1))
    dtable.add_column("🧪 Test ID", style="yellow", width=25)
    dtable.add_column("❓ Question", style="white", width=50, overflow="fold")
    dtable.add_column("Answer", style="bold", justify="center", width=8)
    dtable.add_column("Gold", style="cyan", justify="center", width=8)
    dtable.add_column("Result", style="bold", justify="center", width=10)
    dtable.add_column("💬 Explanation", style="dim", width=50, overflow="fold")

    # Show only failed tests
    failed_rows = [row for row in rows if not row.get("pass", False)]
    for row in failed_rows:
        test_id = row.get("test_id", "?")

        # Get question from input (truncated)
        question = row.get("input", "")
        if "Question:" in question:
            question = question.split("Question:")[-1].split("Choices:")[0].strip()
        question = (question[:47] + "…") if len(question) > 47 else question

        # Extract quiz-specific data from judge_output
        judge_output = row.get("judge_output", {})
        if isinstance(judge_output, dict):
            selected = judge_output.get("selected", "?")
            explanation = judge_output.get("explanation", "No explanation provided")
            passed = judge_output.get("pass", False)
        else:
            selected = "?"
            explanation = str(judge_output)[:40] + "…" if len(str(judge_output)) > 40 else str(judge_output)
            passed = row.get("pass", False)

        # Get correct answer
        correct_answer = row.get("gold", "?")

        # Result with color coding
        if passed:
            result_text = "[bold green]✅ PASS[/bold green]"
        else:
            result_text = "[bold red]❌ FAIL[/bold red]"

        # Truncate explanation to fit
        explanation = (explanation[:47] + "…") if len(explanation) > 47 else explanation

        dtable.add_row(str(test_id), question, str(selected), str(correct_answer), result_text, explanation)

    # Put the table inside the panel
    console.print("\n")
    console.print(
        Panel(
            dtable,
            title=header_text,
            style="cyan",
            padding=(1, 0, 0, 0),
            expand=True,
            width=150,
        )
    )


def _render_code_details_table(console: Console, header_text: str, rows: List[Mapping], category: str):
    """Render details table for code_generation, bug_fixing, and code_agent categories."""
    dtable = Table(show_lines=True, header_style="bold blue", padding=(0, 1))
    dtable.add_column("🧪 Test ID", style="yellow", width=20)
    dtable.add_column("📝 Summary", style="white", width=40, overflow="fold")
    dtable.add_column("📊 Score", style="bold", justify="center", width=10)
    dtable.add_column("✅ Criteria", style="cyan", justify="left", width=12)
    dtable.add_column("💭 Judge Summary", style="dim", width=50, overflow="fold")

    # Show only failed tests (score <= 66% for code/bug fixing/code_agent)
    failed_rows = []
    for row in rows:
        judge_output = row.get("judge_output", {})
        if isinstance(judge_output, dict):
            score = judge_output.get("overall_score", 0.0)
            # Consider failed if score <= 66%
            if score <= 0.66:
                failed_rows.append(row)
        else:
            # Fallback to pass/fail
            if not row.get("pass", False):
                failed_rows.append(row)

    for row in failed_rows:
        test_id = row.get("test_id", "?")

        # Get test summary from the row (if available)
        test_summary = row.get("summary", "")
        if not test_summary and "input" in row:
            # Fallback to truncated input if no summary
            test_summary = (row["input"][:40] + "…") if len(row.get("input", "")) > 40 else row.get("input", "")

        # Extract score and criteria info from judge_output
        judge_output = row.get("judge_output", {})
        if isinstance(judge_output, dict):
            overall_score = judge_output.get("overall_score", 0.0)
            criteria = judge_output.get("criteria", [])
            judge_summary = judge_output.get("summary", "No summary provided")

            # Calculate criteria passed/partial/failed
            if criteria:
                passed_criteria = sum(1 for c in criteria if c.get("pass") is True)
                partial_criteria = sum(1 for c in criteria if c.get("pass") == "partial")
                failed_criteria = sum(1 for c in criteria if c.get("pass") is False)

                criteria_parts = []
                if passed_criteria > 0:
                    criteria_parts.append(f"[bold green]✅ {passed_criteria}[/bold green]")
                if partial_criteria > 0:
                    criteria_parts.append(f"[bold yellow]🟡 {partial_criteria}[/bold yellow]")
                if failed_criteria > 0:
                    criteria_parts.append(f"[bold red]❌ {failed_criteria}[/bold red]")

                criteria_text = "\n".join(criteria_parts) if criteria_parts else "0"
            else:
                criteria_text = "N/A"

            # Format score as percentage with color coding and result status
            score_percent = int(overall_score * 100)
            if score_percent > 66:  # > 66%
                score_text = f"[bold green]✅ {score_percent}%[/bold green]"
            elif score_percent >= 33:  # 33-66%
                score_text = f"[bold yellow]🟡 {score_percent}%[/bold yellow]"
            else:  # < 33%
                score_text = f"[bold red]❌ {score_percent}%[/bold red]"
        else:
            # Fallback for non-structured judge output
            passed = row.get("pass", False)
            score_text = "[green]✅[/green]" if passed else "[red]❌[/red]"
            criteria_text = "N/A"
            judge_summary = str(judge_output)[:50] + "…" if len(str(judge_output)) > 50 else str(judge_output)

        dtable.add_row(str(test_id), test_summary, score_text, criteria_text, judge_summary)

    # Put the table inside the panel
    console.print("\n")
    console.print(
        Panel(
            dtable,
            title=header_text,
            style="cyan",
            padding=(1, 0, 0, 0),
            expand=True,
            width=160,
        )
    )


def _render_generic_details_table(console: Console, header_text: str, rows: List[Mapping]):
    """Fallback details table for unknown categories."""
    dtable = Table(show_lines=True, header_style="bold blue", padding=(0, 1))
    dtable.add_column("🧪 Test ID", style="yellow", width=20)
    dtable.add_column("📊 Result", style="bold", justify="center", width=10)
    dtable.add_column("💭 Output", style="dim", width=80, overflow="fold")

    # Show only failed tests
    failed_rows = [row for row in rows if not row.get("pass", False)]
    for row in failed_rows:
        test_id = row.get("test_id", "?")
        passed = row.get("pass", False)
        result_text = "[green]✅[/green]" if passed else "[red]❌[/red]"
        output = (
            str(row.get("output", ""))[:75] + "…"
            if len(str(row.get("output", ""))) > 75
            else str(row.get("output", ""))
        )

        dtable.add_row(str(test_id), result_text, output)

    console.print("\n")
    console.print(
        Panel(
            dtable,
            title=header_text,
            style="cyan",
            padding=(1, 0, 0, 0),
            expand=True,
            width=120,
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
        elif choice == "p" and page > 0:
            page -= 1
            continue
        elif choice == "n" and page < total_pages - 1:
            page += 1
            continue
        else:
            # User selected a run number
            selected_idx = int(choice) - 1
            return runs_sorted[selected_idx]
