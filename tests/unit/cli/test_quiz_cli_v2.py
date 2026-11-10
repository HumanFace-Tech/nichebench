"""CLI tests for the quiz v2 CLI simplification (Q6).

Covers:

* the run header (``render_run_header``) — quiz reports the configured
  judge model as an ``llm judge``, other categories report the model;
* ``nichebench list-tasks`` — canonical category order, removed
  ``code_agent`` hidden by default and visible (marked non-runnable) with ``--all``;
* ``nichebench show`` — normalized single-lettered quiz choices, the correct
  letter and the dataset version;
* the quiz report — LLM judge model/method, parser cross-check, parse-failure
  count and reasons, and judge-error rows rendered as errors.

No network access and no model endpoint is required.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nichebench.cli.app import app
from nichebench.cli.rich_views.reports.completion import render_run_completion_report
from nichebench.cli.rich_views.run_views import render_run_header
from nichebench.cli.rich_views.tables import render_quiz_case_panel
from nichebench.core.datamodel import TaskSpec
from nichebench.core.datamodel import TestCaseSpec as CaseSpec
from nichebench.core.discovery import discover_frameworks

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[3]
DRUPAL_DATA_DIR = REPO_ROOT / "src" / "nichebench" / "frameworks" / "drupal" / "data"

requires_drupal_data = pytest.mark.skipif(
    not (DRUPAL_DATA_DIR / "quiz").is_dir(),
    reason="private drupal data submodule is not available",
)


def _console(width: int = 200) -> Console:
    """Build a recording console with a fixed width."""
    return Console(record=True, width=width, force_terminal=False)


# ─── run header ───────────────────────────────────────────────────────────────


class TestRenderRunHeader:
    """The run header must be honest about how each category is scored."""

    def test_quiz_header_reports_the_llm_judge(self):
        console = _console()
        render_run_header(
            console,
            mut_model="groq/llama-3.3-70b-versatile",
            judge_model="openai/gpt-5",
            profile=None,
            category="quiz",
        )
        output = console.export_text()
        assert "llm judge" in output
        assert "deterministic" not in output

    def test_quiz_header_shows_the_configured_judge_model(self):
        console = _console()
        render_run_header(
            console,
            mut_model="groq/llama-3.3-70b-versatile",
            judge_model="openai/gpt-5",
            profile=None,
            category="quiz",
        )
        output = console.export_text()
        assert "openai/gpt-5" in output

    def test_code_header_still_shows_the_judge_model(self):
        console = _console()
        render_run_header(
            console,
            mut_model="groq/llama-3.3-70b-versatile",
            judge_model="openai/gpt-5",
            profile="fast",
            category="code_generation",
        )
        output = console.export_text()
        assert "openai/gpt-5" in output
        assert "deterministic" not in output

    def test_header_without_category_keeps_the_judge_model(self):
        """Callers that do not pass a category keep the previous behaviour."""
        console = _console()
        render_run_header(
            console,
            mut_model="groq/llama-3.3-70b-versatile",
            judge_model="openai/gpt-5",
            profile=None,
        )
        assert "openai/gpt-5" in console.export_text()


# ─── list-tasks ───────────────────────────────────────────────────────────────


@requires_drupal_data
class TestListTasksOrdering:
    """``nichebench list-tasks`` uses the canonical category order."""

    def test_quiz_is_listed_first(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        result = runner.invoke(app, ["list-tasks", "drupal"])
        assert result.exit_code == 0, result.output
        output = result.output
        assert "quiz" in output
        quiz_at = output.index("quiz")
        code_at = output.index("code_generation")
        bug_at = output.index("bug_fixing")
        assert quiz_at < code_at < bug_at

    def test_code_agent_is_hidden_by_default(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        result = runner.invoke(app, ["list-tasks", "drupal"])
        assert result.exit_code == 0, result.output
        assert "code_agent" not in result.output

    def test_code_agent_is_shown_and_marked_removed_with_all(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        result = runner.invoke(app, ["list-tasks", "drupal", "--all"])
        assert result.exit_code == 0, result.output
        output = result.output
        assert "code_agent (removed, non-runnable)" in output
        assert "has been removed and is not runnable" in output
        # Never described as merely deprecated — it cannot run at all.
        # (Task summaries may legitimately contain the word "deprecated".)
        assert "code_agent (deprecated)" not in output
        assert "is deprecated" not in output

    def test_quiz_stays_first_with_all(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        result = runner.invoke(app, ["list-tasks", "drupal", "--all"])
        assert result.exit_code == 0, result.output
        output = result.output
        assert output.index("quiz") < output.index("code_generation") < output.index("bug_fixing")
        assert output.index("bug_fixing") < output.index("code_agent")


# ─── show ─────────────────────────────────────────────────────────────────────


@requires_drupal_data
class TestShowQuiz:
    """``nichebench show`` renders normalized quiz choices."""

    def test_show_quiz_has_no_double_letter_prefix(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        result = runner.invoke(app, ["show", "drupal_quiz_004"])
        assert result.exit_code == 0, result.output
        assert "A) A)" not in result.output
        assert "B) B)" not in result.output

    def test_show_quiz_lists_every_choice_once(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        result = runner.invoke(app, ["show", "drupal_quiz_004"])
        assert result.exit_code == 0, result.output
        output = result.output
        for letter in "ABCDEF":
            assert f"{letter})" in output
        # The correct letter is shown as well.
        assert "Correct answer:" in output

    def test_show_quiz_displays_the_dataset_version(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        result = runner.invoke(app, ["show", "drupal_quiz_004"])
        assert result.exit_code == 0, result.output
        output = result.output
        assert "Dataset version:" in output
        assert re.search(r"quiz-v\d", output)

    def test_show_quiz_without_choices_says_so(self, capsys):
        ts = TaskSpec(framework="test", task_type="quiz", file_path="memory")
        tc = CaseSpec(
            id="drupal_quiz_empty",
            type="quiz",
            raw={"id": "drupal_quiz_empty", "question": "Anything?"},
        )
        render_quiz_case_panel("test", ts, tc)
        output = capsys.readouterr().out
        assert "no choices" in output.lower()


class TestShowFindsDeprecatedCategories:
    """``show`` must reach tasks in deprecated categories.

    The listings hide deprecated categories, but the single-case lookup
    must still find them — otherwise ``show <id>`` fails for a task the
    user can see in the data.
    """

    @staticmethod
    def _temp_frameworks(tmp_path: Path) -> dict:
        """Build a framework tree with one deprecated ``code_agent`` task."""
        task_dir = tmp_path / "drupal" / "data" / "code_agent"
        task_dir.mkdir(parents=True)
        (task_dir / "codegen_001.yaml").write_text(
            "id: codegen_001\nsummary: a deprecated task\nprompt: Write a module\n",
            encoding="utf-8",
        )
        return discover_frameworks(tmp_path)

    def test_show_finds_a_deprecated_task(self, tmp_path: Path, monkeypatch):
        frameworks = self._temp_frameworks(tmp_path)
        monkeypatch.setattr(
            "nichebench.cli.commands.show.discover_frameworks",
            lambda root: frameworks,
        )
        result = runner.invoke(app, ["show", "codegen_001"])
        assert result.exit_code == 0, result.output
        assert "codegen_001" in result.output

    def test_deprecated_task_is_still_hidden_from_the_listing(self, tmp_path: Path, monkeypatch):
        """The lookup change must not unhide the category in ``list-tasks``."""
        frameworks = self._temp_frameworks(tmp_path)
        monkeypatch.setattr(
            "nichebench.cli.commands.list.discover_frameworks",
            lambda root: frameworks,
        )
        result = runner.invoke(app, ["list-tasks", "drupal"])
        assert result.exit_code == 0, result.output
        assert "codegen_001" not in result.output

    def test_deprecated_task_is_listed_with_all(self, tmp_path: Path, monkeypatch):
        """``--all`` still surfaces the deprecated category explicitly."""
        frameworks = self._temp_frameworks(tmp_path)
        monkeypatch.setattr(
            "nichebench.cli.commands.list.discover_frameworks",
            lambda root: frameworks,
        )
        result = runner.invoke(app, ["list-tasks", "drupal", "--all"])
        assert result.exit_code == 0, result.output
        assert "codegen_001" in result.output


# ─── report ───────────────────────────────────────────────────────────────────


def _quiz_row(
    *,
    test_id: str,
    passed: bool,
    parse_reason: str,
    selected,
    gold: str,
    parser_selected=None,
    parser_agrees=None,
    judge_output: dict | None = None,
) -> dict:
    """Build a minimal quiz NDJSON row for an LLM-judged quiz run."""
    if judge_output is None:
        judge_output = {
            "pass": passed,
            "selected": selected,
            "score": 1.0 if passed else 0.0,
            "overall_score": 1.0 if passed else 0.0,
            "explanation": "Selected B; gold B" if passed else "no parseable letter",
            "method": "llm",
            "parse_reason": parse_reason,
            "parser": {
                "selected": selected if parser_selected is None else parser_selected,
                "parse_reason": parse_reason,
                "agrees": parser_agrees,
            },
        }
    return {
        "test_id": test_id,
        "framework": "test",
        "category": "quiz",
        "mut_model": "test/mut",
        "judge_model": "openai/gpt-5",
        "judge_method": "llm",
        "pass": passed,
        "summary": "a quiz",
        "gold": gold,
        "input": "Question: Which letter is correct?\nChoices:\nA) a\nB) b",
        "judge_output": judge_output,
    }


class TestQuizReport:
    """The quiz report is LLM-judged and surfaces the parser cross-check."""

    def _write_quiz_run(self, tmp_path: Path) -> tuple[Path, Path]:
        rows = [
            _quiz_row(test_id="quiz_ok", passed=True, parse_reason="ok", selected="B", gold="B", parser_agrees=True),
            _quiz_row(
                test_id="quiz_ambiguous",
                passed=False,
                parse_reason="multiple_letters",
                selected=None,
                gold="B",
            ),
            _quiz_row(
                test_id="quiz_empty",
                passed=False,
                parse_reason="empty",
                selected=None,
                gold="B",
            ),
        ]
        summary = {
            "framework": "test",
            "category": "quiz",
            "model": "test/mut",
            "judge": "openai/gpt-5",
            "judge_method": "llm",
            "dataset_version": "quiz-v2",
            "total": len(rows),
            "passed": 1,
            "partial": 0,
            "failed": 2,
            "avg_score": 1 / 3,
            "parse_failures": 2,
            "parse_failure_reasons": {"multiple_letters": 1, "empty": 1},
        }
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        details_path = tmp_path / "details.jsonl"
        with details_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return summary_path, details_path

    def test_report_shows_parse_failure_count_and_reasons(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setenv("COLUMNS", "200")
        summary_path, details_path = self._write_quiz_run(tmp_path)
        render_run_completion_report(summary_path, details_path)
        output = capsys.readouterr().out

        assert "Parse Failure Reasons" in output
        assert "multiple_letters" in output
        assert "empty" in output
        # The summary table carries the parse-failure count (2).
        assert "2" in output

    def test_report_shows_the_judge_model_and_method(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setenv("COLUMNS", "200")
        summary_path, details_path = self._write_quiz_run(tmp_path)
        render_run_completion_report(summary_path, details_path)
        output = capsys.readouterr().out

        # The judge model travels with the run and the method is ``llm``.
        assert "openai/gpt-5" in output
        assert "llm" in output
        assert "deterministic" not in output

    def test_report_shows_the_parser_cross_check(self, tmp_path: Path, monkeypatch, capsys):
        """A failing row renders whether the deterministic parser agrees."""
        monkeypatch.setenv("COLUMNS", "200")
        rows = [
            _quiz_row(
                test_id="quiz_disagree",
                passed=False,
                parse_reason="ok",
                selected="A",
                gold="B",
                parser_selected="B",
                parser_agrees=False,
            ),
        ]
        summary = {
            "framework": "test",
            "category": "quiz",
            "model": "test/mut",
            "judge": "openai/gpt-5",
            "judge_method": "llm",
            "total": 1,
            "passed": 0,
            "partial": 0,
            "failed": 1,
            "avg_score": 0.0,
            "parse_failures": 0,
            "parse_failure_reasons": {},
        }
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        details_path = tmp_path / "details.jsonl"
        details_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")

        render_run_completion_report(summary_path, details_path)
        output = capsys.readouterr().out

        assert "quiz_disagree" in output
        assert "disagrees" in output
        # The judge's letter is the verdict shown, not the parser's.
        assert "llm judge" in output

    def test_report_renders_judge_error_rows_as_errors(self, tmp_path: Path, monkeypatch, capsys):
        """A quiz judge failure renders as an error line, never a score."""
        monkeypatch.setenv("COLUMNS", "200")
        error_row = _quiz_row(
            test_id="quiz_judge_error",
            passed=False,
            parse_reason="ok",
            selected=None,
            gold="B",
            judge_output={
                "error_kind": "judge_unavailable",
                "error": "[Error: provider down]",
                "attempts": 3,
                "score": None,
                "overall_score": None,
            },
        )
        summary = {
            "framework": "test",
            "category": "quiz",
            "model": "test/mut",
            "judge": "openai/gpt-5",
            "judge_method": "llm",
            "total": 0,
            "attempted_total": 1,
            "judge_errors": 1,
            "passed": 0,
            "partial": 0,
            "failed": 0,
            "avg_score": None,
            "parse_failures": 0,
            "parse_failure_reasons": {},
        }
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        details_path = tmp_path / "details.jsonl"
        details_path.write_text(json.dumps(error_row) + "\n", encoding="utf-8")

        render_run_completion_report(summary_path, details_path)
        output = capsys.readouterr().out

        assert "quiz_judge_error" in output
        assert "judge error (judge_unavailable)" in output
        assert "attempts: 3" in output
        assert "Judge Errors" in output

    def test_report_without_parse_failures_has_no_reasons_table(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setenv("COLUMNS", "200")
        summary_path, details_path = self._write_quiz_run(tmp_path)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["parse_failures"] = 0
        summary["parse_failure_reasons"] = {}
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        render_run_completion_report(summary_path, details_path)
        output = capsys.readouterr().out
        assert "Parse Failure Reasons" not in output
