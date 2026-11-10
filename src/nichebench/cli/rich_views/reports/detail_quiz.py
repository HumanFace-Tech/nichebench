"""Per-test detail table rendering for the ``quiz`` category.

Owner: ``_render_quiz_details_table``.

Shows only failed tests with the question, the judge's selected answer,
the gold answer, the pass/fail result (or an error line for judge /
harness failures), the deterministic parser cross-check
(``agrees`` / ``disagrees`` / ``unknown`` with the parser's letter and
parse reason), and the judge's explanation — plus the attempt count when
the judge needed more than one try.  The panel title carries the scoring
method (``llm judge``).  Quiz scoring is judge-authoritative: the
parser is an audit signal only and never decides the score.
"""

from typing import List, Mapping

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.metrics.types import JUDGE_ERROR_CONFIG

# Placeholder used when a value is missing from the judge output.
_MISSING = "—"


def _truncate(text: str, limit: int) -> str:
    """Shorten ``text`` to ``limit`` characters, marking the cut with an ellipsis."""
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _parser_cell(judge_output: Mapping) -> str:
    """Render the deterministic parser cross-check for one quiz row.

    ``agrees`` / ``disagrees`` / ``unknown`` compare the parser's letter
    with the judge's selected letter; ``unknown`` covers a parser that
    could not select a letter and rows that predate the cross-check.  The
    parser's letter (when it found one) and parse reason are shown on a
    second line for context.
    """
    parser_info = judge_output.get("parser")
    if isinstance(parser_info, dict):
        agrees = parser_info.get("agrees")
        selected = parser_info.get("selected")
        reason = parser_info.get("parse_reason")
    else:
        # Row predates the cross-check: only the parse reason is on record.
        agrees = None
        selected = None
        reason = judge_output.get("parse_reason")

    if agrees is True:
        status = "[green]agrees[/green]"
    elif agrees is False:
        status = "[red]disagrees[/red]"
    else:
        status = "[yellow]unknown[/yellow]"

    if agrees is not None and selected:
        status = f"{status} ({selected})"

    lines = [status]
    if reason:
        lines.append(str(reason))
    return "\n".join(lines)


def _render_quiz_details_table(console: Console, header_text: str, rows: List[Mapping]):
    """Render a per-test detail table for the ``quiz`` category.

    Shows only failed tests with the question, the judge's selected
    answer, the gold answer, the pass/fail (or error) result, the parser
    cross-check, and the judge's explanation — with the attempt count when
    the judge needed more than one try.  Judge infrastructure failures
    (``judge_output.error_kind`` or the row-level ``error_kind``) render
    as an error line with the attempt count instead of a verdict.  The
    scoring method is shown in the panel title.
    """
    dtable = Table(show_lines=True, header_style="bold blue", padding=(0, 1))
    dtable.add_column("🧪 Test ID", style="yellow", width=22)
    dtable.add_column("❓ Question", style="white", width=38, overflow="fold")
    dtable.add_column("Answer", style="bold", justify="center", width=8)
    dtable.add_column("Gold", style="cyan", justify="center", width=6)
    dtable.add_column("Result", style="bold", justify="center", width=10)
    dtable.add_column("🔤 Parser", style="dim", justify="center", width=20, overflow="fold")
    dtable.add_column("📝 Judge Note", style="dim", width=33, overflow="fold")

    # Show only failed tests
    failed_rows = [row for row in rows if not row.get("pass", False)]

    method = "llm"
    for row in failed_rows:
        judge_output = row.get("judge_output", {})
        if isinstance(judge_output, dict):
            row_method = judge_output.get("method")
            if row_method:
                method = str(row_method)
                break

    for row in failed_rows:
        test_id = row.get("test_id", "?")

        # Get question from input (truncated)
        question = row.get("input", "")
        if "Question:" in question:
            question = question.split("Question:")[-1].split("Choices:")[0].strip()
        question = _truncate(question, 38)

        # Extract quiz-specific data from judge_output
        judge_output = row.get("judge_output", {})
        if isinstance(judge_output, dict):
            selected = judge_output.get("selected")
            explanation = judge_output.get("explanation")
            error_kind = judge_output.get("error_kind")
            attempts = judge_output.get("attempts")
        else:
            selected = None
            explanation = str(judge_output) if judge_output else None
            error_kind = None
            attempts = None

        # ``error_kind`` may be nested in the judge output or carried at the
        # row level (``TestResult.to_dict()`` writes it at the top level).
        if not (isinstance(error_kind, str) and error_kind):
            row_error_kind = row.get("error_kind")
            error_kind = row_error_kind if isinstance(row_error_kind, str) and row_error_kind else None

        if not isinstance(attempts, int) or isinstance(attempts, bool):
            row_attempts = row.get("judge_attempts")
            attempts = row_attempts if isinstance(row_attempts, int) and not isinstance(row_attempts, bool) else None

        # Get correct answer
        correct_answer = row.get("gold", "?")

        if error_kind:
            # Infrastructure failure: report the error and how many attempts
            # were made instead of a fabricated verdict or score.
            if str(error_kind) == JUDGE_ERROR_CONFIG:
                note_text = f"config error ({error_kind})"
            else:
                note_text = f"judge error ({error_kind})"
                if isinstance(attempts, int):
                    note_text += f", attempts: {attempts}"
            selected_text = _MISSING
            parser_text = _MISSING
            result_text = "[bold red]⚠️ ERROR[/bold red]"
        else:
            selected_text = _MISSING if selected is None else str(selected)
            parser_text = _parser_cell(judge_output if isinstance(judge_output, Mapping) else {})
            result_text = "[bold red]❌ FAIL[/bold red]"
            note_text = _truncate(str(explanation), 31) if explanation else "No scoring note"
            if isinstance(attempts, int) and attempts > 1:
                note_text = f"{note_text} (attempts: {attempts})"

        dtable.add_row(
            str(test_id),
            question,
            selected_text,
            str(correct_answer),
            result_text,
            parser_text,
            note_text,
        )

    # Put the table inside the panel
    console.print("\n")
    console.print(
        Panel(
            dtable,
            title=f"{header_text}  |  [bold]Scoring:[/bold] [cyan]{method} judge[/cyan]",
            style="cyan",
            padding=(1, 0, 0, 0),
            expand=True,
            width=150,
        )
    )
