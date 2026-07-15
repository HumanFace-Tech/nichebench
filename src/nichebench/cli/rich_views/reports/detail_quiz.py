"""Per-test detail table rendering for the ``quiz`` category.

Owner: ``_render_quiz_details_table``.

Shows only failed tests with the question, model's selected answer,
gold answer, pass/fail result, and the judge's explanation.
"""

from typing import List, Mapping

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def _render_quiz_details_table(console: Console, header_text: str, rows: List[Mapping]):
    """Render a per-test detail table for the ``quiz`` category.

    Shows only failed tests with the question, model's selected answer,
    gold answer, pass/fail result, and the judge's explanation.
    """
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
        result_text = "[bold green]✅ PASS[/bold green]" if passed else "[bold red]❌ FAIL[/bold red]"

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
