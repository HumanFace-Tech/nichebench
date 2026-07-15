"""Per-test detail table rendering for ``code_generation`` and ``bug_fixing`` categories.

Owner: ``_render_code_details_table``.

Shows only failed tests (overall_score ≤ 0.66) with test ID, summary,
score with colour coding, criteria breakdown (passed/partial/failed
counts), and the judge's summary text.
"""

from typing import Mapping, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.execution.summary import normalize_score


def _render_code_details_table(console: Console, header_text: str, rows: Sequence[Mapping], category: str):
    """Render a per-test detail table for code-generation and bug-fixing categories.

    Shows only failed tests (overall_score ≤ 0.66) with test ID, summary,
    score with colour coding, criteria breakdown (passed/partial/failed
    counts), and the judge's summary text.
    """
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
            score = normalize_score(judge_output.get("overall_score"), 0.0)
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
            overall_score = normalize_score(judge_output.get("overall_score"), 0.0)
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
