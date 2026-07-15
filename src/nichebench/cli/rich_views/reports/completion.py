"""Run-completion report rendering.

Owner: ``render_run_completion_report`` entry point and its fallback helper
``_render_generic_details_table``.

Category-specific detail tables (``_render_runtime_details_table``,
``_render_quiz_details_table``, ``_render_code_details_table``) live in their
own modules and are imported here for the routing decision inside
``render_run_completion_report``.

This module only *renders* data that has already been produced by the
executor and written to disk as JSON lines (NDJSON).  It has **no**
knowledge of config loading, task discovery, or execution scheduling.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import List, Mapping, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.cli.rich_views.reports.detail_code import _render_code_details_table
from nichebench.cli.rich_views.reports.detail_quiz import _render_quiz_details_table
from nichebench.cli.rich_views.reports.detail_runtime import (
    _render_runtime_details_table,
)
from nichebench.execution.summary import normalize_score


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
            # Normalize before arithmetic, formatting, comparisons, and conversion.
            # Tolerates numeric strings, invalid values, non-finite values, and
            # out-of-range values without raising.
            avg_score = normalize_score(summary.get("avg_score"), 0.0)
            avg_score_std = normalize_score(summary.get("avg_score_std"), 0.0)
            trials_raw = summary.get("trials", 1)
            try:
                trials = int(trials_raw)
            except (TypeError, ValueError):
                trials = 1
            score_percent = int(avg_score * 100)
            std_percent = int(avg_score_std * 100)
            score_str = f"{score_percent}%"
            if trials > 1 and avg_score_std > 0:
                score_str = f"{score_percent}% ±{std_percent}%"
            if score_percent > 66:
                table_row.append(f"[bold green]{score_str}[/bold green]")
            elif score_percent >= 33:
                table_row.append(f"[bold yellow]{score_str}[/bold yellow]")
            else:
                table_row.append(f"[bold red]{score_str}[/bold red]")
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

            # Detect multi-trial: group rows by test_id
            id_groups_tmp: dict = defaultdict(list)
            for row in rows:
                id_groups_tmp[row.get("test_id", "?")].append(row)
            multi_trial = any(len(v) > 1 for v in id_groups_tmp.values())

            # Count failed tests for this group
            failed_count = 0
            for row in rows:
                if cat in ("code_generation", "bug_fixing", "code_agent"):
                    judge_output = row.get("judge_output", {})
                    if isinstance(judge_output, dict):
                        score = normalize_score(judge_output.get("overall_score"), 0.0)
                        if score <= 0.66:
                            failed_count += 1
                    else:
                        if not row.get("pass", False):
                            failed_count += 1
                else:
                    if not row.get("pass", False):
                        failed_count += 1

            # Skip if no failures to show, but always show runtime tables
            if failed_count == 0 and cat != "runtime":
                continue  # Consistent header formatting and width with failure count

            if cat == "runtime":
                total_count = len(rows)
                trial_info = ""
                if multi_trial:
                    trial_info = f" (multi-trial, {total_count} rows)"
                header_text = (
                    f"[bold]Model:[/bold] [magenta]{mut}[/magenta]  |  "
                    f"[bold]Judged by:[/bold] [cyan]{judge}[/cyan]  |  "
                    f"[bold]Framework:[/bold] [green]{fw}[/green]  |  "
                    f"[bold]Category:[/bold] [yellow]{cat}[/yellow]"
                    f"{trial_info}  |  "
                    f"[bold]Total:[/bold] {total_count}  |  "
                    f"[bold red]❌ Failed: {failed_count}[/bold red]"
                )
            else:
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
            elif cat == "runtime":
                _render_runtime_details_table(console, header_text, rows)
            elif cat in ("code_generation", "bug_fixing", "code_agent"):
                _render_code_details_table(console, header_text, rows, cat)
            else:
                # Fallback for unknown categories
                _render_generic_details_table(console, header_text, rows)


def _render_generic_details_table(console: Console, header_text: str, rows: List[Mapping]):
    """Fallback detail table for any unrecognised category.

    Displays test ID, pass/fail result, and a truncated output string.
    Used when no category-specific renderer is available.
    """
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
