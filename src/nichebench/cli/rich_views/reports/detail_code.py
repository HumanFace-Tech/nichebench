"""Per-test detail table rendering for ``code_generation`` and ``bug_fixing`` categories.

Owner: ``_render_code_details_table``.

Shows only failed tests — the rows whose run verdict is ``pass: false``
(rows from result files that predate the flag are judged against the run's
``evaluation.judge_threshold``) — with test ID, summary, score with colour
coding (yellow once the score reaches ``summary.PARTIAL_SCORE_FLOOR``,
red below it), criteria breakdown (passed/partial/failed
counts), a compact structural sanity indicator (``ok`` / ``advisory`` /
``hard`` plus the finding checks, truncated), and the judge's summary text.
Judge infrastructure failures
(``judge_output.error_kind`` or the row-level ``error_kind``) render as a
"judge error" line with the attempt count instead of a score; harness
configuration failures (``error_kind=config_error``) render as their own
"config error" line so the two infrastructure classes stay apart; and
verdicts that needed more than one judge attempt show their attempt count.

Legacy result files may carry criterion entries that are not mappings (a bare
string, a number, ``null``); those are rendered as a "malformed" placeholder
instead of being read like a criterion, and they are never counted as a
passed or failed criterion.
"""

from typing import Any, Mapping, Optional, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.config.settings import resolve_judge_threshold
from nichebench.execution.summary import PARTIAL_SCORE_FLOOR, normalize_score
from nichebench.metrics.types import JUDGE_ERROR_CONFIG

# Content width of the sanity column (column width 16 minus its padding);
# kept tight so the score/criteria columns keep their exact widths.
_SANITY_CELL_WIDTH = 14


def is_failed_code_row(row: Mapping, judge_threshold: float) -> bool:
    """Whether a code/bug detail row belongs in the failure table.

    ``TestResult.to_dict()`` always writes ``pass`` — the verdict computed
    with the run's effective ``evaluation.judge_threshold`` — so the flag is
    authoritative: a failed result is shown whatever score the judge
    attached, and a passed one is never shown.  Rows from older result files
    without the flag fall back to comparing ``overall_score`` against
    ``judge_threshold``; a row with neither is shown, because an unscorable
    row is a failure.
    """
    if "pass" in row:
        return not bool(row["pass"])
    judge_output = row.get("judge_output")
    if isinstance(judge_output, dict):
        score = normalize_score(judge_output.get("overall_score"), 0.0)
        return score < judge_threshold
    return True


def _sanity_cell(findings: Any) -> str:
    """Render the compact sanity indicator for the detail table.

    ``ok`` (green) when there are no findings, ``hard`` (red) when any
    finding is severity ``hard``, otherwise ``advisory`` (yellow).  The
    finding checks follow one per line, truncated so the column stays
    compact (at most three checks, then a ``+N more`` overflow line).
    """
    if not isinstance(findings, list) or not findings:
        return "[green]ok[/green]"

    checks = [str(item.get("check", "")) for item in findings if isinstance(item, dict) and item.get("check")]
    lines = [_truncate(check, _SANITY_CELL_WIDTH) for check in checks[:3]]
    if len(checks) > 3:
        lines.append(f"+{len(checks) - 3} more")

    if any(isinstance(item, dict) and item.get("severity") == "hard" for item in findings):
        label = "[bold red]hard[/bold red]"
    else:
        label = "[bold yellow]advisory[/bold yellow]"

    return f"{label}\n" + "\n".join(lines) if lines else label


def _truncate(text: str, width: int) -> str:
    """Cut ``text`` to ``width`` characters, marking the cut with an ellipsis."""
    if len(text) <= width:
        return text
    return text[: max(width - 1, 1)] + "…"


def _render_code_details_table(
    console: Console,
    header_text: str,
    rows: Sequence[Mapping],
    category: str,
    evaluation_config: Optional[Mapping] = None,
):
    """Render a per-test detail table for code-generation and bug-fixing categories.

    Shows only the failed tests (``is_failed_code_row``) with test ID,
    summary, score with colour coding, criteria breakdown
    (passed/partial/failed counts), the structural sanity indicator, and the
    judge's summary text.

    Args:
        evaluation_config: Effective evaluation block of the run being
            reported.  Supplies ``judge_threshold`` for rows that predate the
            ``pass`` flag; ``None`` falls back to the environment default.
    """
    judge_threshold = resolve_judge_threshold(evaluation_config)
    dtable = Table(show_lines=True, header_style="bold blue", padding=(0, 1))
    dtable.add_column("🧪 Test ID", style="yellow", width=20)
    dtable.add_column("📝 Summary", style="white", width=40, overflow="fold")
    dtable.add_column("📊 Score", style="bold", justify="center", width=10)
    dtable.add_column("✅ Criteria", style="cyan", justify="left", width=12)
    dtable.add_column("🧩 Sanity", style="white", justify="center", width=16, overflow="fold")
    dtable.add_column("💭 Judge Summary", style="dim", width=50, overflow="fold")

    # Only failed rows reach this table: the run's verdict (``pass``) decides,
    # never the score band alone — a result below a raised threshold is still
    # a failure even when its score looks high.
    failed_rows = [row for row in rows if is_failed_code_row(row, judge_threshold)]

    for row in failed_rows:
        test_id = row.get("test_id", "?")

        # Get test summary from the row (if available)
        test_summary = row.get("summary", "")
        if not test_summary and "input" in row:
            # Fallback to truncated input if no summary
            test_summary = (row["input"][:40] + "…") if len(row.get("input", "")) > 40 else row.get("input", "")

        # Extract score and criteria info from judge_output
        judge_output = row.get("judge_output", {})
        attempts = judge_output.get("attempts") if isinstance(judge_output, dict) else None
        if not isinstance(attempts, int) or isinstance(attempts, bool):
            attempts = row.get("judge_attempts")

        # ``error_kind`` may be nested in the judge output or carried at the row
        # level (``TestResult.to_dict()`` writes it at the top level).
        error_kind = judge_output.get("error_kind") if isinstance(judge_output, dict) else None
        if not (isinstance(error_kind, str) and error_kind):
            row_error_kind = row.get("error_kind")
            error_kind = row_error_kind if isinstance(row_error_kind, str) and row_error_kind else None

        if error_kind:
            # Judge infrastructure failure: report the error and how many
            # attempts were made instead of a fabricated score.  Structural
            # sanity hard-fails are model failures that never reached the
            # judge, and config errors are harness failures, so both are
            # labelled (and de-attempted) separately.
            score_text = "[bold red]error[/bold red]"
            criteria_text = "N/A"
            if str(error_kind).startswith("sanity_"):
                judge_summary = f"sanity hard-fail ({error_kind})"
            elif str(error_kind) == JUDGE_ERROR_CONFIG:
                judge_summary = f"config error ({error_kind})"
            else:
                judge_summary = f"judge error ({error_kind})"
                if isinstance(attempts, int):
                    judge_summary += f", attempts: {attempts}"
        elif isinstance(judge_output, dict):
            overall_score = normalize_score(judge_output.get("overall_score"), 0.0)
            criteria = judge_output.get("criteria", [])
            judge_summary = judge_output.get("summary", "No summary provided")

            # Calculate criteria passed/partial/failed.  Legacy result files
            # may carry criterion entries that are not mappings (a bare
            # string, a number, null); those are rendered as a placeholder
            # instead of being read like a criterion or counted as a pass.
            if isinstance(criteria, list) and criteria:
                well_formed = [c for c in criteria if isinstance(c, Mapping)]
                malformed_count = len(criteria) - len(well_formed)

                passed_criteria = sum(1 for c in well_formed if c.get("pass") is True)
                partial_criteria = sum(1 for c in well_formed if c.get("pass") == "partial")
                failed_criteria = sum(1 for c in well_formed if c.get("pass") is False)

                criteria_parts = []
                if passed_criteria > 0:
                    criteria_parts.append(f"[bold green]✅ {passed_criteria}[/bold green]")
                if partial_criteria > 0:
                    criteria_parts.append(f"[bold yellow]🟡 {partial_criteria}[/bold yellow]")
                if failed_criteria > 0:
                    criteria_parts.append(f"[bold red]❌ {failed_criteria}[/bold red]")
                if malformed_count > 0:
                    # A malformed entry is never counted as passed or failed —
                    # it is flagged, not scored.
                    criteria_parts.append("[bold red]⚠ malformed[/bold red]")
                    judge_summary = (
                        f"{judge_summary}, malformed criterion"
                        if isinstance(judge_summary, str)
                        else "malformed criterion"
                    )

                # A malformed-only list still renders its placeholder; a list of
                # well-formed criteria with no recognised ``pass`` value keeps
                # the original "0" readout.
                criteria_text = "\n".join(criteria_parts) if criteria_parts else "0"
            else:
                criteria_text = "N/A"

            # Colour follows the summary bands: every row here failed, so it
            # is partial (yellow) once the score reaches the partial floor and
            # a fail (red) below it.  Green never appears — passed rows are
            # filtered out before this table is built.
            score_percent = int(overall_score * 100)
            if overall_score >= PARTIAL_SCORE_FLOOR:
                score_text = f"[bold yellow]🟡 {score_percent}%[/bold yellow]"
            else:
                score_text = f"[bold red]❌ {score_percent}%[/bold red]"

            # Surface retry count when the judge needed more than one try.
            if isinstance(attempts, int) and attempts > 1:
                judge_summary = f"{judge_summary}, attempts: {attempts}"
        else:
            # Fallback for non-structured judge output
            passed = row.get("pass", False)
            score_text = "[green]✅[/green]" if passed else "[red]❌[/red]"
            criteria_text = "N/A"
            judge_summary = str(judge_output)[:50] + "…" if len(str(judge_output)) > 50 else str(judge_output)

        # Structural sanity indicator (C4): prefer the row-level findings
        # written by ``TestResult.to_dict()``, fall back to the judge copy.
        sanity_findings = row.get("sanity_findings")
        if not isinstance(sanity_findings, list) and isinstance(judge_output, dict):
            sanity_findings = judge_output.get("sanity_findings")
        sanity_text = _sanity_cell(sanity_findings)

        dtable.add_row(str(test_id), test_summary, score_text, criteria_text, sanity_text, judge_summary)

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
