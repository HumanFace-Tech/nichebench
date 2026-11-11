"""Run-completion report rendering.

Owner: ``render_run_completion_report`` entry point and its fallback helper
``_render_generic_details_table``.

Category-specific detail tables (``_render_runtime_details_table``,
``_render_quiz_details_table``, ``_render_code_details_table``) live in their
own modules and are imported here for the routing decision inside
``render_run_completion_report``.

This module only *renders* data that has already been produced by the
executor and written to disk as JSON lines (NDJSON) or as ``summary.json``.
It has **no** knowledge of config loading, task discovery, or execution
scheduling: the pass threshold it reports with is the ``judge_threshold``
the run recorded inside ``summary.json`` (falling back to the environment
default when the run predates the key).
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import List, Mapping, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.cli.rich_views.reports.detail_code import (
    _render_code_details_table,
    is_failed_code_row,
)
from nichebench.cli.rich_views.reports.detail_quiz import _render_quiz_details_table
from nichebench.cli.rich_views.reports.detail_runtime import (
    _render_runtime_details_table,
)
from nichebench.config.settings import resolve_judge_threshold
from nichebench.execution.summary import normalize_score
from nichebench.metrics.types import JUDGE_ERROR_CONFIG


def _summary_evaluation_config(summary: Mapping) -> Optional[Mapping]:
    """Return the effective evaluation block a run recorded in ``summary.json``."""
    config = summary.get("config")
    if not isinstance(config, Mapping):
        return None
    evaluation = config.get("evaluation")
    return evaluation if isinstance(evaluation, Mapping) else None


def _is_judge_or_config_error_row(row: Mapping) -> bool:
    """Return ``True`` when a runtime row failed for judge/config reasons.

    Such rows carry no score: the judge never produced a usable verdict
    (``judge_*``) or the harness itself failed (``config_error``).  They say
    nothing about the model, so they are not counted as failed model tests.
    """
    judge_output = row.get("judge_output")
    kind = judge_output.get("error_kind") if isinstance(judge_output, Mapping) else None
    if not (isinstance(kind, str) and kind):
        kind = row.get("error_kind")
    if not (isinstance(kind, str) and kind):
        return False
    return kind.startswith("judge_") or kind == JUDGE_ERROR_CONFIG


def render_run_completion_report(summary_path: Path, details_path: Optional[Path] = None):
    """Render a summary report after a run (called by RUN command)."""
    console = Console()
    if not summary_path.exists():
        console.print(f"[red]No summary.json found at {summary_path}")
        return
    with open(summary_path) as f:
        summary = json.load(f)

    # Pass threshold of the run being reported (``evaluation.judge_threshold``
    # when the run recorded one, otherwise the environment default).  Used to
    # count and render failures for the LLM-judged categories.
    evaluation_config = _summary_evaluation_config(summary)
    judge_threshold = resolve_judge_threshold(evaluation_config)

    # Clean summary table - exclude config and other verbose data
    is_quiz = summary.get("category") == "quiz"
    is_code = summary.get("category") in ("code_generation", "bug_fixing", "code_agent")
    is_runtime = summary.get("category") == "runtime"
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

    col_titles: dict = {}
    if is_quiz:
        # Quiz is judged by an LLM like every other category: the summary
        # reports the scoring method, and the average score is the pass rate
        # over the scored rows.  The judge *model* travels in ``summary.json``
        # (``judge``) and in the run header / detail panel.
        display_keys = [
            "framework",
            "category",
            "model",
            "judge_method",
            "total",
            "results",
            "avg_score",
        ]
        col_titles["judge_method"] = "🎯 Scoring"
        col_titles["avg_score"] = "🎯 Pass Rate"
        # Judge-error and config-error rows are excluded from the pass rate,
        # so a quiz run that carries any has to say why its total is below
        # the rows it ran.
        quiz_extras = [key for key in ("attempted_total", "judge_errors") if key in summary]
        insert_at = display_keys.index("results")
        display_keys[insert_at:insert_at] = quiz_extras
        if "attempted_total" in quiz_extras:
            col_titles["attempted_total"] = "🔢 Attempted Total"
        if "judge_errors" in quiz_extras:
            col_titles["judge_errors"] = "⚠️ Judge Errors"
        dataset_version = summary.get("dataset_version")
        if dataset_version:
            display_keys.append("dataset_version")
            col_titles["dataset_version"] = "📦 Dataset"
        parse_failures = summary.get("parse_failures") or 0
        try:
            parse_failures = int(parse_failures)
        except (TypeError, ValueError):
            parse_failures = 0
        if parse_failures > 0:
            display_keys.append("parse_failures")
            col_titles["parse_failures"] = "⚠️ Parse Failures"
        # Config failures are excluded from the pass rate, so a quiz run that
        # carries any has to say why its total is below the rows it ran.
        config_errors = summary.get("config_errors") or 0
        try:
            config_errors = int(config_errors)
        except (TypeError, ValueError):
            config_errors = 0
        if config_errors > 0:
            display_keys.append("config_errors")
            col_titles["config_errors"] = "⚠️ Config Errors"

    if is_code:
        # Judge-error and config-error rows are excluded from ``total`` and
        # ``avg_score`` by the aggregator, so the summary reports the raw
        # attempted count and each infrastructure-failure count — labelled
        # separately — alongside the scored total.
        extras = [key for key in ("attempted_total", "judge_errors", "config_errors") if key in summary]
        insert_at = display_keys.index("results")
        display_keys[insert_at:insert_at] = extras
        if "attempted_total" in extras:
            col_titles["attempted_total"] = "🔢 Attempted Total"
        if "judge_errors" in extras:
            col_titles["judge_errors"] = "⚠️ Judge Errors"
        if "config_errors" in extras:
            col_titles["config_errors"] = "⚠️ Config Errors"

    if is_runtime:
        # Runtime rows whose judge never answered are excluded from ``total``
        # and ``avg_score`` by the aggregator, exactly like the code
        # categories: the report must say how many rows were attempted and
        # how many failed for infrastructure reasons instead of showing them
        # as model failures.
        extras = [key for key in ("attempted_total", "judge_errors", "config_errors") if key in summary]
        insert_at = display_keys.index("results")
        display_keys[insert_at:insert_at] = extras
        if "attempted_total" in extras:
            col_titles["attempted_total"] = "🔢 Attempted Total"
        if "judge_errors" in extras:
            col_titles["judge_errors"] = "⚠️ Judge Errors"
        if "config_errors" in extras:
            col_titles["config_errors"] = "⚠️ Config Errors"

    # Add vertical space before summary
    console.print("\n")
    table = Table(
        title="[bold green]Run Summary[/bold green]",
        header_style="bold magenta",
        width=160,
        padding=(0, 1),
    )

    for key in display_keys:
        if key in col_titles:
            col_title = col_titles[key]
        elif key in emoji_map:
            col_title = f"{emoji_map.get(key, '')} {key.replace('_', ' ').title()}"
        else:
            col_title = key.replace("_", " ").title()
        table.add_column(
            col_title,
            style="cyan" if key in ("framework", "category", "model", "judge", "judge_method") else "white",
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
        elif key == "avg_score" and "avg_score" in summary and summary["avg_score"] is None:
            # Nothing could be scored (every row was a judge or config error)
            # — show an honest "n/a" instead of a fabricated 0%.
            table_row.append("n/a")
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
        elif key == "judge_method" and is_quiz:
            # Quiz is judged by an LLM; older summaries without the key fall
            # back to the only method a quiz run can have.
            table_row.append(str(summary.get("judge_method") or "llm"))
        elif key == "parse_failures":
            table_row.append(str(summary.get("parse_failures", 0)))
        elif key in summary:
            val = summary[key]
            table_row.append(str(val))
        else:
            table_row.append("N/A")
    table.add_row(*table_row)
    console.print(table)

    # Quiz runs additionally report the parse-failure taxonomy.
    if is_quiz:
        _render_parse_failure_reasons(console, summary)

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
            judge_error_count = 0
            for row in rows:
                # A row whose judge never answered carries no score: it is an
                # infrastructure failure, not a failed model test.  This holds
                # for every category, not just runtime.
                if _is_judge_or_config_error_row(row):
                    judge_error_count += 1
                    continue
                if cat in ("code_generation", "bug_fixing", "code_agent"):
                    # Same predicate as the detail table: the run's verdict
                    # decides, so a failure never falls outside the count just
                    # because its score cleared the old 0.66 display band.
                    if is_failed_code_row(row, judge_threshold):
                        failed_count += 1
                else:
                    if not row.get("pass", False):
                        failed_count += 1

            # Skip if there is nothing to show.  Judge/config-error-only groups
            # still render so the excluded rows are visible, and runtime tables
            # always render.
            if failed_count == 0 and judge_error_count == 0 and cat != "runtime":
                continue  # Consistent header formatting and width with failure count

            if cat == "runtime":
                # Judge/config error rows are excluded from the scored total
                # and from the failure count: they are reported separately.
                total_count = len(rows) - judge_error_count
                trial_info = ""
                if multi_trial:
                    trial_info = f" (multi-trial, {len(rows)} rows)"
                judge_error_info = (
                    f"  |  [bold yellow]⚠️ Judge Errors: {judge_error_count}[/bold yellow]"
                    if judge_error_count
                    else ""
                )
                header_text = (
                    f"[bold]Model:[/bold] [magenta]{mut}[/magenta]  |  "
                    f"[bold]Judged by:[/bold] [cyan]{judge}[/cyan]  |  "
                    f"[bold]Framework:[/bold] [green]{fw}[/green]  |  "
                    f"[bold]Category:[/bold] [yellow]{cat}[/yellow]"
                    f"{trial_info}  |  "
                    f"[bold]Total:[/bold] {total_count}  |  "
                    f"[bold red]❌ Failed: {failed_count}[/bold red]"
                    f"{judge_error_info}"
                )
            elif cat == "quiz":
                # Quiz is judged by an LLM — the judge model travels with the
                # run; the scoring method is appended by the quiz renderer.
                judge_error_info = (
                    f"  |  [bold yellow]⚠️ Judge Errors: {judge_error_count}[/bold yellow]"
                    if judge_error_count
                    else ""
                )
                header_text = (
                    f"[bold]Model:[/bold] [magenta]{mut}[/magenta]  |  "
                    f"[bold]Judged by:[/bold] [cyan]{judge}[/cyan]  |  "
                    f"[bold]Framework:[/bold] [green]{fw}[/green]  |  "
                    f"[bold]Category:[/bold] [yellow]{cat}[/yellow]  |  "
                    f"[bold red]❌ Failed Tests: {failed_count}[/bold red]"
                    f"{judge_error_info}"
                )
            else:
                judge_error_info = (
                    f"  |  [bold yellow]⚠️ Judge Errors: {judge_error_count}[/bold yellow]"
                    if judge_error_count
                    else ""
                )
                header_text = (
                    f"[bold]Model:[/bold] [magenta]{mut}[/magenta]  |  "
                    f"[bold]Judged by:[/bold] [cyan]{judge}[/cyan]  |  "
                    f"[bold]Framework:[/bold] [green]{fw}[/green]  |  "
                    f"[bold]Category:[/bold] [yellow]{cat}[/yellow]  |  "
                    f"[bold red]❌ Failed Tests: {failed_count}[/bold red]"
                    f"{judge_error_info}"
                )

            # Create different table layouts based on category
            if cat == "quiz":
                _render_quiz_details_table(console, header_text, rows)
            elif cat == "runtime":
                _render_runtime_details_table(console, header_text, rows)
            elif cat in ("code_generation", "bug_fixing", "code_agent"):
                _render_code_details_table(console, header_text, rows, cat, evaluation_config)
            else:
                # Fallback for unknown categories
                _render_generic_details_table(console, header_text, rows)


def _render_parse_failure_reasons(console: Console, summary: Mapping) -> None:
    """Show the parse-failure taxonomy of a quiz run.

    Only rendered when the summary reports at least one parse failure and
    carries a reason breakdown.  The reasons are ordered by frequency so
    the most common failure mode is visible first.
    """
    reasons = summary.get("parse_failure_reasons")
    if not isinstance(reasons, Mapping) or not reasons:
        return

    try:
        total = int(summary.get("parse_failures") or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return

    def _count(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    table = Table(
        title="[bold yellow]Parse Failure Reasons[/bold yellow]",
        header_style="bold magenta",
        padding=(0, 1),
        min_width=40,
    )
    table.add_column("Reason", style="white")
    table.add_column("Count", style="bold yellow", justify="right")
    for reason, count in sorted(reasons.items(), key=lambda item: (-_count(item[1]), str(item[0]))):
        table.add_row(str(reason), str(count))
    console.print(table)


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
