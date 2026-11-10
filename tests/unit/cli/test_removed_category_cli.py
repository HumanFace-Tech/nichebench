"""CLI behaviour for the removed ``code_agent`` category.

Covers the code_agent-removal critique:

* **Count consistency** — the no-arg ``list-tasks`` overview must count the
  same tasks as ``list`` and ``list-tasks <framework>`` for the same ``--all``
  value: removed categories are excluded by default and only counted with
  ``--all`` (historical discovery).
* **Wording** — the category is marked *removed / non-runnable*, never
  "deprecated", and ``--all`` help/notes say so explicitly.
* **Historical report rendering** — summary and detail reports produced by
  past ``code_agent`` runs still render (code-category summary extras, code
  detail-table routing, judge-error rows).

No network access and no model endpoint is required.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from nichebench.cli.app import app
from nichebench.cli.rich_views.reports.completion import render_run_completion_report
from nichebench.core.discovery import discover_frameworks

runner = CliRunner()


# ─── Fixture: a temp framework with 2 runnable + 3 removed tasks ──────────────


def _temp_frameworks(tmp_path: Path) -> dict:
    """Build a framework tree with 2 quiz and 3 removed ``code_agent`` tasks."""
    quiz_dir = tmp_path / "drupal" / "data" / "quiz"
    quiz_dir.mkdir(parents=True, exist_ok=True)
    for i in (1, 2):
        (quiz_dir / f"quiz_{i:03d}.yaml").write_text(
            f"id: quiz_{i:03d}\nsummary: quiz case {i}\n"
            "question: Which letter is correct?\nchoices: [a, b]\ncorrect_choice: A\n",
            encoding="utf-8",
        )
    ca_dir = tmp_path / "drupal" / "data" / "code_agent"
    ca_dir.mkdir(parents=True, exist_ok=True)
    for i in (1, 2, 3):
        (ca_dir / f"codegen_{i:03d}.yaml").write_text(
            f"id: codegen_{i:03d}\nsummary: historical case {i}\nprompt: Write a module\n",
            encoding="utf-8",
        )
    return discover_frameworks(tmp_path)


def _invoke(monkeypatch, tmp_path: Path, args: list[str]) -> str:
    """Invoke the CLI against the temp framework and return captured output."""
    frameworks = _temp_frameworks(tmp_path)
    monkeypatch.setattr(
        "nichebench.cli.commands.list.discover_frameworks",
        lambda root: frameworks,
    )
    monkeypatch.setenv("COLUMNS", "200")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def _overview_total(output: str) -> int:
    """Extract the drupal total from the no-arg ``list-tasks`` overview table."""
    match = re.search(r"drupal\s+(\d+)", output)
    assert match, f"overview row not found in:\n{output}"
    return int(match.group(1))


def _frameworks_table_total(output: str) -> int:
    """Extract the Framework Total cell of the quiz row from ``list`` output."""
    match = re.search(r"drupal\s*│\s*quiz\s*│\s*\d+\s*│\s*(\d+)", output)
    assert match, f"quiz row not found in:\n{output}"
    return int(match.group(1))


def _listed_test_ids(output: str) -> list[str]:
    """Return every test ID rendered in a ``list-tasks <framework>`` table."""
    return re.findall(r"\b(?:quiz|codegen)_\d{3}\b", output)


# ─── Count consistency ────────────────────────────────────────────────────────


class TestOverviewCountConsistency:
    """The no-arg ``list-tasks`` overview must not count removed tasks."""

    def test_default_overview_excludes_removed_category(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list-tasks"])
        # 2 quiz tasks only — the 3 code_agent tasks are removed/non-runnable.
        assert _overview_total(output) == 2

    def test_all_overview_includes_removed_category(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list-tasks", "--all"])
        assert _overview_total(output) == 5

    def test_default_overview_matches_frameworks_table(self, monkeypatch, tmp_path):
        """``list-tasks`` (no arg) and ``list`` must agree by default."""
        overview = _invoke(monkeypatch, tmp_path, ["list-tasks"])
        frameworks_table = _invoke(monkeypatch, tmp_path, ["list"])
        assert _overview_total(overview) == _frameworks_table_total(frameworks_table) == 2

    def test_all_overview_matches_frameworks_table(self, monkeypatch, tmp_path):
        """With ``--all`` both views must count the removed tasks too."""
        overview = _invoke(monkeypatch, tmp_path, ["list-tasks", "--all"])
        frameworks_table = _invoke(monkeypatch, tmp_path, ["list", "--all"])
        assert _overview_total(overview) == _frameworks_table_total(frameworks_table) == 5

    def test_default_overview_matches_task_row_count(self, monkeypatch, tmp_path):
        overview = _invoke(monkeypatch, tmp_path, ["list-tasks"])
        tasks = _invoke(monkeypatch, tmp_path, ["list-tasks", "drupal"])
        assert _overview_total(overview) == len(_listed_test_ids(tasks)) == 2

    def test_all_overview_matches_task_row_count(self, monkeypatch, tmp_path):
        overview = _invoke(monkeypatch, tmp_path, ["list-tasks", "--all"])
        tasks = _invoke(monkeypatch, tmp_path, ["list-tasks", "drupal", "--all"])
        assert _overview_total(overview) == len(_listed_test_ids(tasks)) == 5


# ─── Removed / non-runnable wording ───────────────────────────────────────────


class TestRemovedCategoryWording:
    """Listings must say removed/non-runnable, never deprecated."""

    def test_default_overview_does_not_name_the_removed_category(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list-tasks"])
        assert "code_agent" not in output
        assert "removed" in output
        assert "deprecated" not in output

    def test_all_overview_notes_removed_and_not_runnable(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list-tasks", "--all"])
        assert "code_agent has been removed and is not runnable" in output
        assert "deprecated" not in output

    def test_all_tasks_listing_marks_category_removed_non_runnable(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list-tasks", "drupal", "--all"])
        assert "code_agent (removed, non-runnable)" in output
        assert "deprecated" not in output

    def test_all_frameworks_listing_marks_category_removed_non_runnable(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list", "--all"])
        assert "code_agent (removed, non-runnable)" in output
        assert "deprecated" not in output

    def test_all_option_help_says_removed_non_runnable(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list-tasks", "--help"])
        assert "removed, non-runnable" in output
        assert "deprecated" not in output

    def test_list_all_option_help_says_removed_non_runnable(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list", "--help"])
        assert "removed, non-runnable" in output
        assert "deprecated" not in output

    def test_default_task_listing_still_hides_removed_ids(self, monkeypatch, tmp_path):
        output = _invoke(monkeypatch, tmp_path, ["list-tasks", "drupal"])
        assert "codegen_" not in output
        assert "quiz_001" in output


# ─── Historical code_agent report rendering ───────────────────────────────────


def _code_agent_row(judge_output, *, test_id: str, passed: bool = False) -> dict:
    """Build a minimal historical ``code_agent`` NDJSON row."""
    return {
        "test_id": test_id,
        "framework": "drupal",
        "category": "code_agent",
        "mut_model": "test/mut",
        "judge_model": "test/judge",
        "pass": passed,
        "summary": "a historical code_agent task",
        "judge_output": judge_output,
    }


class TestHistoricalCodeAgentReportRendering:
    """Past ``code_agent`` result files must keep rendering unchanged."""

    @staticmethod
    def _write_run(tmp_path: Path, rows: list[dict], **summary_overrides) -> tuple[Path, Path]:
        summary = {
            "framework": "drupal",
            "category": "code_agent",
            "model": "test/mut",
            "judge": "test/judge",
            "judge_method": "llm",
            "total": len(rows),
            "attempted_total": len(rows),
            "judge_errors": 0,
            "passed": sum(1 for r in rows if r.get("pass")),
            "partial": 0,
            "failed": sum(1 for r in rows if not r.get("pass")),
            "avg_score": 0.5,
        }
        summary.update(summary_overrides)
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        details_path = tmp_path / "details.jsonl"
        with details_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return summary_path, details_path

    def test_summary_renders_as_a_code_category(self, tmp_path, capsys, monkeypatch):
        """The code summary extras (attempted/judge errors) apply to code_agent."""
        monkeypatch.setenv("COLUMNS", "200")
        summary_path, _ = self._write_run(tmp_path, [], attempted_total=2, judge_errors=1)

        render_run_completion_report(summary_path)

        output = capsys.readouterr().out
        assert "code_agent" in output
        assert "Attempted Total" in output
        assert "Judge Errors" in output

    def test_failing_detail_row_routes_to_the_code_table(self, tmp_path, capsys, monkeypatch):
        """A failed historical row renders through the code detail table."""
        monkeypatch.setenv("COLUMNS", "200")
        rows = [
            _code_agent_row(
                {"overall_score": 0.2, "criteria": [], "summary": "needs work"},
                test_id="ca_fail",
            )
        ]
        summary_path, details_path = self._write_run(tmp_path, rows, avg_score=0.2)

        render_run_completion_report(summary_path, details_path)

        output = capsys.readouterr().out
        assert "ca_fail" in output
        # Percentage rendered by the code detail table, not the generic fallback.
        assert "20%" in output

    def test_passing_detail_row_renders_without_error(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        rows = [
            _code_agent_row(
                {"overall_score": 0.95, "criteria": []},
                test_id="ca_pass",
                passed=True,
            )
        ]
        summary_path, details_path = self._write_run(tmp_path, rows, avg_score=0.95)

        render_run_completion_report(summary_path, details_path)

        output = capsys.readouterr().out
        assert "code_agent" in output
        # Passing rows are excluded from the failure detail table.
        assert "ca_pass" not in output

    def test_judge_error_row_renders_error_not_a_score(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        rows = [
            _code_agent_row(
                {
                    "error_kind": "judge_unavailable",
                    "error": "[Error: provider down]",
                    "attempts": 3,
                    "judge_score": None,
                    "final_score": None,
                },
                test_id="ca_err",
            )
        ]
        summary_path, details_path = self._write_run(
            tmp_path, rows, avg_score=None, judge_errors=1, attempted_total=1, total=0
        )

        render_run_completion_report(summary_path, details_path)

        output = capsys.readouterr().out
        assert "ca_err" in output
        assert "judge_unavailable" in output
        # Judge infrastructure failures never render as a fabricated score.
        assert "0%" not in output
        assert "100%" not in output
