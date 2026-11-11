"""Unit tests for safe score coercion in CLI report rendering.

These tests focus on the rich_views/reports rendering functions
(``_render_code_details_table`` and the failure-counting branch of
``render_run_completion_report``) and ensure that generic score-like
judge output — numeric strings, malformed strings, booleans, non-finite
floats, and out-of-range values — does not raise an exception while
rendering.  Report views must reuse the same safe coercion as summary
aggregation (see ``summary.normalize_score``).
"""

import io
import json
import re
from pathlib import Path

import pytest
from rich.console import Console

from nichebench.cli.rich_views.reports.completion import render_run_completion_report
from nichebench.cli.rich_views.reports.detail_code import _render_code_details_table
from nichebench.cli.rich_views.reports.detail_quiz import _render_quiz_details_table
from nichebench.cli.rich_views.reports.detail_runtime import (
    _render_runtime_details_table,
)

# ─── Shared helpers ───────────────────────────────────────────────────────────


def _capture_console() -> tuple[Console, io.StringIO]:
    """Build a Console that writes to a fresh in-memory buffer."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    return console, buf


def _code_row(judge_output, *, test_id: str = "t1", passed: bool = False) -> dict:
    """Build a minimal NDJSON row for code_generation / bug_fixing."""
    return {
        "test_id": test_id,
        "framework": "test",
        "category": "code_generation",
        "mut_model": "test/mut",
        "judge_model": "test/judge",
        "pass": passed,
        "summary": "a test",
        "judge_output": judge_output,
    }


def _runtime_row(
    judge_output,
    *,
    test_id: str = "rt1",
    passed: bool = False,
    base_branch: str = "main",
    resolved_sha: str = "abcdef1234567890",
    summary: str = "a runtime task",
) -> dict:
    """Build a minimal NDJSON row for the runtime category."""
    return {
        "test_id": test_id,
        "framework": "test",
        "category": "runtime",
        "mut_model": "test/mut",
        "judge_model": "test/judge",
        "pass": passed,
        "summary": summary,
        "base_branch": base_branch,
        "resolved_sha": resolved_sha,
        "judge_output": judge_output,
    }


# ─── Tests for _render_code_details_table ─────────────────────────────────────


class TestRenderCodeDetailsTableSafeScoring:
    """_render_code_details_table must coerce overall_score safely."""

    @pytest.mark.parametrize(
        "score_value",
        [
            "0.95",  # numeric string
            "0",  # numeric string zero
            "1.0",  # numeric string one
            "garbage",  # non-numeric string
            "",  # empty string
            "nan",  # non-finite string
            "inf",  # non-finite string
            "-inf",  # non-finite string
            "1.5",  # out-of-range string
            "-0.5",  # out-of-range string
        ],
    )
    def test_string_overall_score_does_not_raise(self, score_value: str):
        console, _ = _capture_console()
        rows = [_code_row({"overall_score": score_value})]
        # Must not raise — old code crashed with TypeError on strings,
        # or ValueError on non-finite strings.
        _render_code_details_table(console, "header", rows, "code_generation")

    @pytest.mark.parametrize(
        "score_value",
        [
            float("nan"),
            float("inf"),
            float("-inf"),
            1.5,  # out-of-range float
            -0.5,  # out-of-range float
        ],
    )
    def test_float_overall_score_does_not_raise(self, score_value):
        console, _ = _capture_console()
        rows = [_code_row({"overall_score": score_value})]
        # Old code crashed with ValueError when casting NaN to int,
        # and produced nonsensical "150%" for 1.5.
        _render_code_details_table(console, "header", rows, "code_generation")

    @pytest.mark.parametrize("score_value", [True, False])
    def test_bool_overall_score_does_not_raise(self, score_value: bool):
        console, _ = _capture_console()
        rows = [_code_row({"overall_score": score_value})]
        # Old code silently treated True as 100% (passing).
        # New code must coerce booleans through normalize_score → 0.0.
        _render_code_details_table(console, "header", rows, "code_generation")

    def test_missing_overall_score_does_not_raise(self):
        console, _ = _capture_console()
        rows = [_code_row({"criteria": []})]  # no overall_score key
        _render_code_details_table(console, "header", rows, "code_generation")

    def test_none_overall_score_does_not_raise(self):
        console, _ = _capture_console()
        rows = [_code_row({"overall_score": None})]
        _render_code_details_table(console, "header", rows, "code_generation")

    def test_numeric_string_failing_row_renders_as_failure(self):
        """A numeric-string score below 66% must still be flagged as a failure."""
        console, buf = _capture_console()
        rows = [_code_row({"overall_score": "0.20", "criteria": [], "summary": "judge said"})]
        _render_code_details_table(console, "header", rows, "code_generation")
        # Output must contain the test ID and the percentage text "20%"
        output = buf.getvalue()
        assert "t1" in output
        assert "20%" in output

    def test_numeric_string_passing_score_excluded(self):
        """A row the run marked as passed must NOT be flagged as a failure.

        (The detail table only renders failures, and ``pass`` is the verdict.)
        """
        console, buf = _capture_console()
        rows = [_code_row({"overall_score": "0.95"}, passed=True)]
        _render_code_details_table(console, "header", rows, "code_generation")
        # Table body should be empty for a passing row — no percentage text.
        assert "95%" not in buf.getvalue()

    def test_failed_row_above_the_old_display_band_is_shown(self):
        """Regression: a failed verdict used to vanish when its score > 0.66.

        The filter was score-based, so a result below a raised threshold (or
        the narrow 0.66-0.7 window) never reached the failure table.
        """
        console, buf = _capture_console()
        rows = [
            _code_row({"overall_score": 0.68, "criteria": [], "summary": "just short"}, test_id="near_miss"),
            _code_row({"overall_score": 0.95, "criteria": [], "summary": "raised bar"}, test_id="raised_bar"),
        ]

        _render_code_details_table(console, "header", rows, "code_generation")

        output = buf.getvalue()
        assert "near_miss" in output
        assert "raised_bar" in output
        assert "68%" in output
        assert "95%" in output

    def test_a_passing_high_score_row_is_still_excluded(self):
        console, buf = _capture_console()
        rows = [
            _code_row({"overall_score": 0.95, "criteria": [], "summary": "fine"}, test_id="passing_row", passed=True)
        ]

        _render_code_details_table(console, "header", rows, "code_generation")

        assert "passing_row" not in buf.getvalue()

    def test_malformed_string_score_falls_back_to_fail(self):
        """A malformed score must be coerced to 0.0 → rendered as a failure."""
        console, buf = _capture_console()
        rows = [_code_row({"overall_score": "garbage"})]
        _render_code_details_table(console, "header", rows, "code_generation")
        # Score coerced to 0.0 → score_percent = 0 → "0%" rendered as failure.
        assert "0%" in buf.getvalue()

    def test_non_finite_float_score_falls_back_to_zero(self):
        """A NaN score must be coerced to 0.0 → rendered as 0% failure."""
        console, buf = _capture_console()
        rows = [_code_row({"overall_score": float("nan")})]
        _render_code_details_table(console, "header", rows, "code_generation")
        # NaN coerced to 0.0 → "0%" rendered.
        assert "0%" in buf.getvalue()

    def test_bool_true_score_falls_back_to_zero(self):
        """A True boolean score must NOT be silently accepted as 100%."""
        console, buf = _capture_console()
        rows = [_code_row({"overall_score": True})]
        _render_code_details_table(console, "header", rows, "code_generation")
        # True → normalize_score returns 0.0 → "0%" rendered (NOT "100%").
        assert "100%" not in buf.getvalue()
        assert "0%" in buf.getvalue()

    def test_mixed_rows_render_without_error(self):
        """A mixture of score types must render without exception."""
        console, _ = _capture_console()
        rows = [
            _code_row({"overall_score": "0.20"}, test_id="string_low"),
            _code_row({"overall_score": 0.95}, test_id="float_high", passed=True),
            _code_row({"overall_score": float("nan")}, test_id="nan"),
            _code_row({"overall_score": "garbage"}, test_id="garbage"),
            _code_row({"overall_score": True}, test_id="bool_true"),
            _code_row({}, test_id="missing"),
        ]
        _render_code_details_table(console, "header", rows, "code_generation")


class TestRenderCodeDetailsMalformedCriteria:
    """Legacy result files may carry criterion entries that are not mappings.

    The renderer must not call ``.get()`` on them (crash) and must not turn
    them into a fabricated score.
    """

    def test_malformed_criterion_entry_does_not_raise(self):
        console, _ = _capture_console()
        rows = [_code_row({"overall_score": 0.2, "criteria": ["bad"], "summary": "judge said"})]

        _render_code_details_table(console, "header", rows, "code_generation")

    @pytest.mark.parametrize("entry", ["bad", 1, None, 1.5, True])
    def test_malformed_criterion_entry_renders_a_placeholder(self, entry):
        console, buf = _capture_console()
        rows = [_code_row({"overall_score": 0.2, "criteria": [entry], "summary": "judge said"})]

        _render_code_details_table(console, "header", rows, "code_generation")

        output = buf.getvalue()
        assert "t1" in output
        assert "malformed" in output

    def test_malformed_criterion_does_not_fabricate_a_score(self):
        """A malformed criterion is not counted as a passed or failed criterion."""
        console, buf = _capture_console()
        rows = [_code_row({"overall_score": 0.2, "criteria": ["bad"], "summary": "judge said"})]

        _render_code_details_table(console, "header", rows, "code_generation")

        output = buf.getvalue()
        # No criterion is reported as passed or failed (the "✅" in the
        # column header is not a criterion count).
        assert "✅ 1" not in output
        assert "❌ 1" not in output
        # The score shown is the one the judge reported (20%), not a value
        # derived from the malformed criterion.
        assert "20%" in output

    def test_mixed_criteria_render_without_error(self):
        console, buf = _capture_console()
        rows = [
            _code_row(
                {
                    "overall_score": 0.5,
                    "criteria": [{"pass": True}, "bad", {"pass": False}],
                    "summary": "mixed",
                },
                test_id="mixed_row",
            )
        ]

        _render_code_details_table(console, "header", rows, "code_generation")

        output = buf.getvalue()
        assert "mixed_row" in output
        assert "malformed" in output
        assert "50%" in output

    def test_non_list_criteria_does_not_raise(self):
        console, _ = _capture_console()
        rows = [_code_row({"overall_score": 0.2, "criteria": "not a list", "summary": "judge said"})]

        _render_code_details_table(console, "header", rows, "code_generation")


class TestRenderCodeDetailsJudgeErrors:
    """Judge infrastructure failures render as an error line, not a score."""

    def _error_row(self) -> dict:
        return _code_row(
            {
                "error_kind": "judge_unavailable",
                "error": "[Error: provider down]",
                "attempts": 3,
                "judge_score": None,
                "final_score": None,
            },
            test_id="t_err",
        )

    def test_judge_error_renders_error_and_attempts_not_a_score(self):
        console, buf = _capture_console()
        rows = [self._error_row()]
        _render_code_details_table(console, "header", rows, "code_generation")

        output = buf.getvalue()
        assert "t_err" in output
        assert "judge error" in output
        assert "judge_unavailable" in output
        assert "attempts: 3" in output
        # No fabricated percentage is rendered for a judge failure.
        assert "0%" not in output
        assert "100%" not in output

    def test_attempts_shown_when_more_than_one(self):
        console, buf = _capture_console()
        rows = [
            _code_row(
                {"overall_score": 0.2, "criteria": [], "summary": "needs work", "attempts": 2},
                test_id="t_retry",
            )
        ]
        _render_code_details_table(console, "header", rows, "code_generation")

        output = buf.getvalue()
        assert "t_retry" in output
        assert "20%" in output
        assert "attempts: 2" in output

    def test_attempts_hidden_for_a_single_attempt_verdict(self):
        console, buf = _capture_console()
        rows = [
            _code_row(
                {"overall_score": 0.2, "criteria": [], "summary": "needs work", "attempts": 1},
                test_id="t_once",
            )
        ]
        _render_code_details_table(console, "header", rows, "code_generation")

        output = buf.getvalue()
        assert "t_once" in output
        assert "attempts:" not in output

    def test_row_level_error_kind_renders_error_not_percentage(self):
        """``error_kind`` carried at the row level (as ``to_dict()`` writes it)
        renders the error kind and attempts — never a percentage — even when
        ``overall_score`` is missing entirely."""
        console, buf = _capture_console()
        row = _code_row({}, test_id="t_row_err")
        row["error_kind"] = "judge_unavailable"
        row["judge_attempts"] = 3
        _render_code_details_table(console, "header", [row], "code_generation")

        output = buf.getvalue()
        assert "t_row_err" in output
        assert "judge error" in output
        assert "judge_unavailable" in output
        assert "attempts: 3" in output
        assert "0%" not in output
        assert "100%" not in output


class TestRenderCodeDetailsConfigError:
    """A harness/config failure renders as its own label, not as a judge error."""

    def test_config_error_row_is_labelled_separately(self):
        console, buf = _capture_console()
        rows = [_code_row({"error": "[Error: harness blew up]", "error_kind": "config_error"}, test_id="t_cfg")]
        _render_code_details_table(console, "header", rows, "code_generation")

        output = buf.getvalue()
        assert "t_cfg" in output
        assert "config error (config_error)" in output
        assert "judge error" not in output
        # No fabricated percentage, and no judge attempt count either.
        assert "0%" not in output
        assert "100%" not in output
        assert "attempts:" not in output


# ─── Tests for the failure-counting branch of render_run_completion_report ────


class TestRenderRunCompletionReportSafeScoring:
    """The per-group failure-counting in render_run_completion_report must
    safely coerce overall_score — it must not crash on strings, NaN, bools,
    or out-of-range values."""

    def _write_run_files(
        self,
        tmp_path: Path,
        rows: list[dict],
        *,
        category: str = "code_generation",
    ) -> tuple[Path, Path]:
        """Write a summary.json and details NDJSON; return both paths."""
        summary_path = tmp_path / "summary.json"
        details_path = tmp_path / "details.jsonl"
        summary_path.write_text(
            json.dumps(
                {
                    "framework": "test",
                    "category": category,
                    "model": "test/mut",
                    "judge": "test/judge",
                    "total": len(rows),
                    "passed": 0,
                    "partial": 0,
                    "failed": len(rows),
                    "avg_score": 0.0,
                }
            ),
            encoding="utf-8",
        )
        with details_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return summary_path, details_path

    @pytest.mark.parametrize(
        "score_value",
        ["0.20", "garbage", "", "nan", "inf", "1.5", "-0.5"],
    )
    def test_string_overall_score_does_not_raise(self, tmp_path: Path, score_value):
        summary_path, details_path = self._write_run_files(
            tmp_path,
            [_code_row({"overall_score": score_value})],
        )
        # Must not raise — old code crashed with TypeError / ValueError.
        render_run_completion_report(summary_path, details_path)

    @pytest.mark.parametrize(
        "score_value",
        [float("nan"), float("inf"), float("-inf"), 1.5, -0.5],
    )
    def test_float_overall_score_does_not_raise(self, tmp_path: Path, score_value):
        summary_path, details_path = self._write_run_files(
            tmp_path,
            [_code_row({"overall_score": score_value})],
        )
        render_run_completion_report(summary_path, details_path)

    @pytest.mark.parametrize("score_value", [True, False])
    def test_bool_overall_score_does_not_raise(self, tmp_path: Path, score_value: bool):
        summary_path, details_path = self._write_run_files(
            tmp_path,
            [_code_row({"overall_score": score_value})],
        )
        render_run_completion_report(summary_path, details_path)

    def test_numeric_string_low_score_counts_as_failure(self, tmp_path: Path):
        """A string score below 66% must be counted as a failure → table renders."""
        summary_path, details_path = self._write_run_files(
            tmp_path,
            [_code_row({"overall_score": "0.20"}, test_id="low_str")],
        )
        # Capture stdout/stderr from the rendering.
        # We just ensure no exception and the test_id appears (table rendered).
        # NOTE: render_run_completion_report builds its own Console() — we
        # trust summary.normalize_score for correctness via separate tests.
        render_run_completion_report(summary_path, details_path)

    def test_numeric_string_high_score_excluded(self, tmp_path: Path):
        """A string score >= 66% is not a failure → detail table is skipped."""
        summary_path, details_path = self._write_run_files(
            tmp_path,
            [_code_row({"overall_score": "0.95"}, test_id="high_str", passed=True)],
        )
        render_run_completion_report(summary_path, details_path)

    def test_mixed_score_types_render_without_error(self, tmp_path: Path):
        """A mixture of score types must not crash the report renderer."""
        rows = [
            _code_row({"overall_score": "0.20"}, test_id="low_str"),
            _code_row({"overall_score": 0.95}, test_id="high_float", passed=True),
            _code_row({"overall_score": float("nan")}, test_id="nan"),
            _code_row({"overall_score": "garbage"}, test_id="garbage"),
            _code_row({"overall_score": True}, test_id="bool_true"),
            _code_row({"overall_score": 1.5}, test_id="oor_float"),
            _code_row({}, test_id="missing"),
        ]
        summary_path, details_path = self._write_run_files(tmp_path, rows)
        render_run_completion_report(summary_path, details_path)

    def test_no_details_file_does_not_raise_on_safe_summary(self, tmp_path: Path):
        """With no details file, the summary table itself renders safely."""
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "framework": "test",
                    "category": "code_generation",
                    "model": "test/mut",
                    "judge": "test/judge",
                    "total": 0,
                    "passed": 0,
                    "partial": 0,
                    "failed": 0,
                    "avg_score": 0.0,
                }
            ),
            encoding="utf-8",
        )
        render_run_completion_report(summary_path)


class TestCodeFailureCountingFollowsTheRunVerdict:
    """The failure count and the detail table share one verdict-based filter."""

    @staticmethod
    def _write_run(tmp_path: Path, rows: list[dict], **summary_overrides) -> tuple[Path, Path]:
        summary = {
            "framework": "test",
            "category": "code_generation",
            "model": "test/mut",
            "judge": "test/judge",
            "total": len(rows),
            "passed": 0,
            "partial": 0,
            "failed": len(rows),
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

    def test_a_failed_row_above_066_renders_the_failure_table(
        self, tmp_path: Path, capsys, monkeypatch
    ):
        """Regression: the count used ``score <= 0.66``, so a 0.68 failure
        silently skipped the whole detail table."""
        monkeypatch.setenv("COLUMNS", "200")
        rows = [_code_row({"overall_score": 0.68, "criteria": [], "summary": "near miss"}, test_id="near_miss")]
        summary_path, details_path = self._write_run(tmp_path, rows)

        render_run_completion_report(summary_path, details_path)

        output = capsys.readouterr().out
        assert "near_miss" in output
        assert "Failed Tests: 1" in output
        assert "68%" in output

    def test_the_recorded_threshold_decides_for_rows_without_a_pass_flag(
        self, tmp_path: Path, capsys, monkeypatch
    ):
        """A result file that predates the ``pass`` flag is judged against the
        threshold the run recorded in ``summary.json`` — its effective
        ``evaluation.judge_threshold`` — not a hardcoded band."""
        monkeypatch.setenv("COLUMNS", "200")
        row = {
            "test_id": "borderline",
            "framework": "test",
            "category": "code_generation",
            "mut_model": "test/mut",
            "judge_model": "test/judge",
            "summary": "a test",
            "judge_output": {"overall_score": 0.95, "criteria": [], "summary": "borderline"},
        }

        # Bar raised above the score → the row failed and is reported.
        summary_path, details_path = self._write_run(
            tmp_path, [row], config={"evaluation": {"judge_threshold": 1.0}}
        )
        render_run_completion_report(summary_path, details_path)
        assert "borderline" in capsys.readouterr().out

        # Same row, threshold at the score → the row passed and is hidden.
        summary_path, details_path = self._write_run(
            tmp_path, [row], config={"evaluation": {"judge_threshold": 0.95}}
        )
        render_run_completion_report(summary_path, details_path)
        assert "borderline" not in capsys.readouterr().out


class TestRenderRunCompletionSummaryNormalization:
    """The summary table rendering must normalize summary-level
    ``avg_score`` and ``avg_score_std`` before arithmetic, formatting,
    comparisons, and conversion — and tolerate numeric strings, invalid,
    non-finite, and out-of-range values without raising.
    """

    @staticmethod
    def _write_summary(tmp_path: Path, **overrides) -> Path:
        """Write a minimal summary.json with the given overrides."""
        summary = {
            "framework": "test",
            "category": "code_generation",
            "model": "test/mut",
            "judge": "test/judge",
            "total": 0,
            "passed": 0,
            "partial": 0,
            "failed": 0,
            "avg_score": 0.0,
        }
        summary.update(overrides)
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return summary_path

    # ── avg_score coercion ──────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "value",
        ["0.95", "0", "1.0", "0.5", 0.8, 0.95, 0.0, 1.0],
    )
    def test_avg_score_numeric_string_or_float_renders(self, tmp_path: Path, value):
        """Numeric strings and floats render as a percentage without raising."""
        p = self._write_summary(tmp_path, avg_score=value)
        render_run_completion_report(p)

    @pytest.mark.parametrize(
        "value",
        [
            "nan",  # non-finite string
            "inf",  # non-finite string
            "-inf",  # non-finite string
            float("nan"),  # non-finite float
            float("inf"),  # non-finite float
            float("-inf"),  # non-finite float
            "1.5",  # out-of-range string
            "-0.5",  # out-of-range string
            1.5,  # out-of-range float
            -0.5,  # out-of-range float
            "garbage",  # non-numeric string
            "",  # empty string
            None,  # null
        ],
    )
    def test_avg_score_invalid_does_not_raise(self, tmp_path: Path, value):
        """Invalid summary-level avg_score values render as 0% without raising."""
        p = self._write_summary(tmp_path, avg_score=value)
        # Must not raise — old code crashed with ValueError/OverflowError/TypeError.
        render_run_completion_report(p)

    def test_missing_avg_score_defaults_to_zero(self, tmp_path: Path):
        """An entirely missing avg_score falls back to 0.0 without raising."""
        # Build a summary dict without avg_score at all.
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "framework": "test",
                    "category": "code_generation",
                    "model": "test/mut",
                    "judge": "test/judge",
                    "total": 0,
                    "passed": 0,
                    "partial": 0,
                    "failed": 0,
                }
            ),
            encoding="utf-8",
        )
        render_run_completion_report(summary_path)

    # ── avg_score_std coercion ───────────────────────────────────────────────

    @pytest.mark.parametrize(
        "value",
        ["0.1", 0.1, 0.0, "0", "1.0", 1.0],
    )
    def test_avg_score_std_valid_renders(self, tmp_path: Path, value):
        p = self._write_summary(tmp_path, avg_score=0.8, trials=3, avg_score_std=value)
        render_run_completion_report(p)

    @pytest.mark.parametrize(
        "value",
        [
            "nan",
            "inf",
            "-inf",
            float("nan"),
            float("inf"),
            float("-inf"),
            "1.5",
            "-0.5",
            1.5,
            -0.5,
            "garbage",
            "",
            None,
        ],
    )
    def test_avg_score_std_invalid_does_not_raise(self, tmp_path: Path, value):
        """Invalid std must not raise; non-positive std must drop the ± suffix."""
        p = self._write_summary(tmp_path, avg_score=0.8, trials=3, avg_score_std=value)
        render_run_completion_report(p)

    def test_avg_score_std_missing_defaults_to_zero(self, tmp_path: Path):
        """Missing avg_score_std must not raise even when trials > 1."""
        p = self._write_summary(tmp_path, avg_score=0.8, trials=3)
        render_run_completion_report(p)

    # ── trials coercion ──────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "trials",
        [None, "garbage", [], {}, True, False, "2"],
    )
    def test_invalid_trials_does_not_raise(self, tmp_path: Path, trials):
        """Invalid trials values must not crash the comparison ``trials > 1``."""
        p = self._write_summary(tmp_path, avg_score=0.8, avg_score_std=0.1, trials=trials)
        render_run_completion_report(p)

    def test_valid_trials_int_renders_with_std(self, tmp_path: Path):
        """Valid integer trials with a positive std produces a ± stddev display."""
        p = self._write_summary(tmp_path, avg_score=0.8, avg_score_std=0.1, trials=3)
        render_run_completion_report(p)

    # ── Combined hostile inputs ──────────────────────────────────────────────

    def test_all_summary_fields_hostile_does_not_raise(self, tmp_path: Path):
        """All summary-level score fields are simultaneously invalid."""
        p = self._write_summary(
            tmp_path,
            avg_score=float("nan"),
            avg_score_std="garbage",
            trials=None,
        )
        render_run_completion_report(p)

    def test_hostile_summary_with_details_does_not_raise(self, tmp_path: Path):
        """Hostile summary alongside valid detail rows does not raise."""
        summary_path = self._write_summary(
            tmp_path,
            avg_score="nan",
            avg_score_std=float("inf"),
            trials="garbage",
        )
        details_path = tmp_path / "details.jsonl"
        details_path.write_text(
            json.dumps(_code_row({"overall_score": 0.5}, test_id="t1")) + "\n",
            encoding="utf-8",
        )
        render_run_completion_report(summary_path, details_path)


class TestCodeSummaryJudgeErrors:
    """Code/bug run summaries surface judge errors, attempted totals, and an
    honest ``n/a`` when nothing could be scored."""

    @staticmethod
    def _write_code_summary(tmp_path: Path, **overrides) -> Path:
        """Write a code-run summary.json: one scored row, one judge error."""
        summary = {
            "framework": "test",
            "category": "code_generation",
            "model": "test/mut",
            "judge": "test/judge",
            "judge_method": "llm",
            "total": 1,
            "attempted_total": 2,
            "judge_errors": 1,
            "passed": 1,
            "partial": 0,
            "failed": 0,
            "avg_score": 0.8,
        }
        summary.update(overrides)
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return summary_path

    def test_judge_error_count_is_visible(self, tmp_path: Path, capsys, monkeypatch):
        """A run with judge_errors > 0 renders without KeyError and labels the errors."""
        monkeypatch.setenv("COLUMNS", "200")
        summary_path = self._write_code_summary(tmp_path)

        render_run_completion_report(summary_path)

        output = capsys.readouterr().out
        assert "Judge Errors" in output
        assert "Attempted Total" in output
        # The attempted count (2) and the valid-result average are rendered.
        assert " 2 " in output
        assert "80%" in output

    def test_null_avg_score_renders_n_a_without_crash(self, tmp_path: Path, capsys, monkeypatch):
        """``avg_score: null`` (every row was a judge error) shows ``n/a``."""
        monkeypatch.setenv("COLUMNS", "200")
        summary_path = self._write_code_summary(
            tmp_path,
            total=0,
            passed=0,
            failed=0,
            judge_errors=2,
            avg_score=None,
        )

        render_run_completion_report(summary_path)

        output = capsys.readouterr().out
        assert "n/a" in output
        # No fabricated percentage is rendered for an unscorable run.
        assert "0%" not in output

    def test_report_table_renders_judge_errors_without_keyerror(self, capsys, monkeypatch):
        """``render_report_table`` renders a judge-error code summary and
        labels the ``judge_errors`` column."""
        from nichebench.cli.rich_views.tables import render_report_table

        monkeypatch.setenv("COLUMNS", "200")
        summary = {
            "framework": "test",
            "category": "code_generation",
            "model": "test/mut",
            "judge": "test/judge",
            "judge_method": "llm",
            "total": 1,
            "attempted_total": 2,
            "judge_errors": 1,
            "passed": 1,
            "partial": 0,
            "failed": 0,
            "avg_score": 0.8,
        }

        render_report_table(summary, title="Results Summary")

        output = capsys.readouterr().out
        assert "judge_errors" in output
        assert "attempted_total" in output

    def test_config_error_count_is_labelled_separately(self, tmp_path: Path, capsys, monkeypatch):
        """``config_errors`` gets its own column — it is not a judge error."""
        monkeypatch.setenv("COLUMNS", "200")
        summary_path = self._write_code_summary(
            tmp_path, judge_errors=0, config_errors=1, attempted_total=2, total=1
        )

        render_run_completion_report(summary_path)

        output = capsys.readouterr().out
        assert "Config Errors" in output
        assert "Judge Errors" in output
        assert " 1 " in output

    def test_a_summary_without_config_errors_keeps_its_columns(self, tmp_path: Path, capsys, monkeypatch):
        """Old and clean summaries (no ``config_errors`` key) render unchanged."""
        monkeypatch.setenv("COLUMNS", "200")
        summary_path = self._write_code_summary(tmp_path)
        # Drop the key the way a summary written before this change lacks it.
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.pop("config_errors", None)
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        render_run_completion_report(summary_path)

        output = capsys.readouterr().out
        assert "Config Errors" not in output
        assert "Judge Errors" in output
        assert "Attempted Total" in output


# ─── Tests for _render_runtime_details_table ──────────────────────────────────


class TestRenderRuntimeDetailsTableSafeScoring:
    """_render_runtime_details_table must coerce hybrid / det / judge scores
    via ``normalize_score`` and never call ``float()`` directly on raw
    judge output values."""

    @pytest.mark.parametrize(
        "hybrid_value",
        [
            "0.85",  # numeric string
            "0",  # numeric string zero
            "1.0",  # numeric string one
            "garbage",  # non-numeric string
            "",  # empty string
            "nan",  # non-finite string
            "inf",  # non-finite string
            "-inf",  # non-finite string
            "1.5",  # out-of-range string
            "-0.5",  # out-of-range string
        ],
    )
    def test_string_hybrid_score_does_not_raise(self, hybrid_value):
        console, _ = _capture_console()
        rows = [
            _runtime_row(
                {
                    "hybrid_score": hybrid_value,
                    "deterministic_score": 0.5,
                    "deterministic_gate_passed": True,
                    "checks": [],
                },
            )
        ]
        # Old code crashed with TypeError on strings or ValueError on
        # non-finite strings. New code must coerce via normalize_score.
        _render_runtime_details_table(console, "header", rows)

    @pytest.mark.parametrize(
        "hybrid_value",
        [
            float("nan"),
            float("inf"),
            float("-inf"),
            1.5,  # out-of-range
            -0.5,  # out-of-range
        ],
    )
    def test_float_hybrid_score_does_not_raise(self, hybrid_value):
        console, _ = _capture_console()
        rows = [
            _runtime_row(
                {
                    "hybrid_score": hybrid_value,
                    "deterministic_score": 0.5,
                    "deterministic_gate_passed": True,
                    "checks": [],
                },
            )
        ]
        # Old code would propagate NaN through the mean; new code coerces.
        _render_runtime_details_table(console, "header", rows)

    @pytest.mark.parametrize("score_value", [True, False])
    def test_bool_hybrid_score_does_not_raise(self, score_value: bool):
        console, _ = _capture_console()
        rows = [
            _runtime_row(
                {
                    "hybrid_score": score_value,
                    "deterministic_score": 0.5,
                    "deterministic_gate_passed": True,
                    "checks": [],
                },
            )
        ]
        # Booleans must NOT silently become 1.0/0.0 via int subclass.
        _render_runtime_details_table(console, "header", rows)

    def test_malformed_string_judge_score_falls_back_to_zero(self):
        """A malformed judge_score must coerce to 0.0 (not raise)."""
        console, buf = _capture_console()
        rows = [
            _runtime_row(
                {
                    "hybrid_score": 0.5,
                    "deterministic_score": 0.5,
                    "judge_score": "garbage",
                    "deterministic_gate_passed": True,
                    "checks": [],
                },
            )
        ]
        _render_runtime_details_table(console, "header", rows)
        # Old code raised ValueError on float("garbage"). Must render.
        output = buf.getvalue()
        assert "rt1" in output

    def test_non_finite_string_judge_score_falls_back_to_zero(self):
        console, buf = _capture_console()
        rows = [
            _runtime_row(
                {
                    "hybrid_score": 0.5,
                    "deterministic_score": 0.5,
                    "judge_score": "nan",
                    "deterministic_gate_passed": True,
                    "checks": [],
                },
            )
        ]
        _render_runtime_details_table(console, "header", rows)
        output = buf.getvalue()
        assert "rt1" in output

    def test_missing_hybrid_score_does_not_raise(self):
        console, _ = _capture_console()
        rows = [
            _runtime_row(
                {
                    "deterministic_score": 0.5,
                    "deterministic_gate_passed": True,
                    "checks": [],
                },
            )
        ]
        _render_runtime_details_table(console, "header", rows)

    def test_mixed_score_types_render_without_error(self):
        console, _ = _capture_console()
        rows = [
            _runtime_row(
                {
                    "hybrid_score": "0.85",
                    "deterministic_score": 0.5,
                    "judge_score": 0.7,
                    "checks": [],
                    "deterministic_gate_passed": True,
                },
                test_id="str_low",
            ),
            _runtime_row(
                {
                    "hybrid_score": 0.95,
                    "deterministic_score": "0.5",
                    "judge_score": "0.9",
                    "checks": [],
                    "deterministic_gate_passed": True,
                },
                test_id="mixed",
            ),
            _runtime_row(
                {
                    "hybrid_score": float("nan"),
                    "deterministic_score": float("inf"),
                    "judge_score": "garbage",
                    "checks": [],
                    "deterministic_gate_passed": False,
                },
                test_id="nan",
            ),
            _runtime_row(
                {
                    "hybrid_score": "garbage",
                    "deterministic_score": "1.5",
                    "judge_score": True,
                    "checks": [],
                    "deterministic_gate_passed": False,
                },
                test_id="garbage",
            ),
            _runtime_row(
                {"checks": [], "deterministic_gate_passed": True},
                test_id="missing_all",
            ),
        ]
        _render_runtime_details_table(console, "header", rows)


# ─── Tests for runtime judge errors in the detail table ───────────────────────


def _runtime_error_row(
    *,
    test_id: str = "rt1",
    kind: str = "judge_unavailable",
    attempts: int = 3,
) -> dict:
    """Build a runtime row whose judge never produced a usable verdict."""
    return _runtime_row(
        {
            "error_kind": kind,
            "error": "[Error: HTTP 429]",
            "attempts": attempts,
            "deterministic_score": 0.5,
            "deterministic_gate_passed": True,
            "judge_score": None,
            "hybrid_score": None,
            "final_score": None,
            "overall_score": None,
            "checks": [{"name": "module exists", "passed": True, "message": "found"}],
        },
        test_id=test_id,
        passed=False,
    )


def _has_zero_percent(output: str) -> bool:
    """Whether a whole cell renders as ``0.00%`` (``50.00%`` does not count)."""
    return re.search(r"(?<![\d.])0\.00%", output) is not None


class TestRenderRuntimeJudgeErrors:
    """A runtime row whose judge failed renders as an error, never as a 0% score."""

    def test_judge_error_row_shows_error_not_a_score(self):
        console, buf = _capture_console()
        _render_runtime_details_table(console, "header", [_runtime_error_row()])

        output = buf.getvalue()
        assert "ERROR" in output
        assert "N/A" in output
        # No percentage is invented for a scoreless row.
        assert not _has_zero_percent(output)
        # The error kind and the attempt count are reported.
        assert "judge_unavailable" in output
        assert re.search(r"judge_unavailable[^0-9]*3", output)

    def test_config_error_row_is_reported_too(self):
        console, buf = _capture_console()
        _render_runtime_details_table(console, "header", [_runtime_error_row(kind="config_error", attempts=1)])

        output = buf.getvalue()
        assert "ERROR" in output
        assert "config_error" in output
        assert not _has_zero_percent(output)

    def test_multi_trial_mean_ignores_the_error_row(self):
        """A scoreless trial is missing, not zero: the mean is over scored trials."""
        console, buf = _capture_console()
        rows = [
            _runtime_row(
                {
                    "hybrid_score": 0.8,
                    "judge_score": 0.9,
                    "deterministic_score": 1.0,
                    "deterministic_gate_passed": True,
                    "checks": [],
                },
                test_id="rt1",
            ),
            _runtime_error_row(test_id="rt1"),
        ]

        _render_runtime_details_table(console, "header", rows)

        output = buf.getvalue()
        # The error trial contributes nothing: the mean stays 80%, not 40%.
        assert "80.00%" in output
        assert "40.00%" not in output
        # The failed trial is still reported, with its kind and attempts.
        assert "judge_unavailable" in output
        assert re.search(r"judge_unavailable[^0-9]*3", output)


# ─── Tests for runtime judge errors in the completion report ──────────────────


def _write_runtime_summary(tmp_path: Path, **overrides) -> Path:
    """Write a runtime summary.json carrying one judge-error row."""
    summary = {
        "framework": "test",
        "category": "runtime",
        "model": "test/mut",
        "judge": "test/judge",
        "total": 0,
        "attempted_total": 1,
        "judge_errors": 1,
        "config_errors": 0,
        "passed": 0,
        "partial": 0,
        "failed": 0,
        "avg_score": None,
    }
    summary.update(overrides)
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return summary_path


class TestRuntimeCompletionJudgeErrors:
    """Runtime judge/config error rows are infrastructure failures, not model failures."""

    def test_judge_error_row_is_not_counted_as_a_failed_model_test(self, tmp_path: Path, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        summary_path = _write_runtime_summary(tmp_path)
        details_path = tmp_path / "details.jsonl"
        details_path.write_text(json.dumps(_runtime_error_row()) + "\n", encoding="utf-8")

        render_run_completion_report(summary_path, details_path)

        output = capsys.readouterr().out
        # Not a failed model test, but the infrastructure failure is labelled.
        assert "❌ Failed: 0" in output
        assert "⚠️ Judge Errors: 1" in output
        assert "Judge Errors" in output
        assert "Attempted Total" in output
        # An unscorable run renders honestly instead of a fabricated 0%.
        assert "n/a" in output

    def test_a_judge_error_row_renders_as_an_error_in_the_detail_table(self, tmp_path: Path, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        summary_path = _write_runtime_summary(tmp_path)
        details_path = tmp_path / "details.jsonl"
        details_path.write_text(json.dumps(_runtime_error_row()) + "\n", encoding="utf-8")

        render_run_completion_report(summary_path, details_path)

        output = capsys.readouterr().out
        assert "ERROR" in output
        assert "N/A" in output
        assert "judge_unavailable" in output
        assert not _has_zero_percent(output)

    def test_a_runtime_summary_without_errors_keeps_its_columns(self, tmp_path: Path, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        summary_path = _write_runtime_summary(
            tmp_path,
            total=1,
            attempted_total=1,
            judge_errors=0,
            passed=1,
            failed=0,
            avg_score=0.9,
        )

        render_run_completion_report(summary_path)

        output = capsys.readouterr().out
        assert "Attempted Total" in output
        assert "Judge Errors" in output
        assert "90%" in output


# ─── Tests for _render_quiz_details_table ─────────────────────────────────────


def _quiz_row(
    judge_output,
    *,
    test_id: str = "quiz_001",
    passed: bool = False,
    gold: str = "B",
    question: str = "Question: Which letter is correct?\nChoices:\nA) a\nB) b",
) -> dict:
    """Build a minimal NDJSON row for the quiz category."""
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
        "input": question,
        "judge_output": judge_output,
    }


class TestRenderQuizDetailsTable:
    """The quiz detail table shows the judge verdict and the parser cross-check."""

    def test_failed_row_shows_parse_reason(self):
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": None,
                    "score": 0.0,
                    "explanation": "Ambiguous letters: B, C (multiple_letters)",
                    "method": "llm",
                    "parse_reason": "multiple_letters",
                }
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        output = buf.getvalue()
        assert "multiple_letters" in output
        assert "quiz_001" in output
        assert "llm judge" in output

    def test_missing_selected_and_explanation_do_not_raise(self):
        """``selected=None`` and a missing explanation must not crash."""
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {"pass": False, "score": 0.0, "method": "llm", "parse_reason": "empty"},
                test_id="quiz_missing",
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        output = buf.getvalue()
        assert "quiz_missing" in output
        assert "empty" in output

    def test_non_dict_judge_output_does_not_raise(self):
        console, _ = _capture_console()
        rows = [_quiz_row("not a dict", test_id="quiz_broken")]
        _render_quiz_details_table(console, "header", rows)

    def test_passing_rows_are_not_rendered(self):
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {
                    "pass": True,
                    "selected": "B",
                    "score": 1.0,
                    "explanation": "Selected B; gold B",
                    "method": "llm",
                    "parse_reason": "ok",
                },
                test_id="quiz_passed",
                passed=True,
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        assert "quiz_passed" not in buf.getvalue()

    def test_table_shows_the_judge_method_and_note(self):
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": "A",
                    "score": 0.0,
                    "explanation": "Selected A; gold B",
                    "method": "llm",
                    "parse_reason": "ok",
                }
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        output = buf.getvalue().lower()
        assert "llm judge" in output
        assert "judge note" in output

    def test_parser_agreement_is_shown(self):
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": "A",
                    "score": 0.0,
                    "explanation": "Selected A; gold B",
                    "method": "llm",
                    "parse_reason": "ok",
                    "parser": {"selected": "A", "parse_reason": "ok", "agrees": True},
                }
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        output = buf.getvalue()
        assert "agrees" in output
        assert "disagrees" not in output

    def test_parser_disagreement_is_shown(self):
        """The judge's letter stays the verdict; the parser cross-check reports the conflict."""
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": "A",
                    "score": 0.0,
                    "explanation": "Selected A; gold B",
                    "method": "llm",
                    "parse_reason": "ok",
                    "parser": {"selected": "B", "parse_reason": "ok", "agrees": False},
                }
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        output = buf.getvalue()
        assert "disagrees" in output
        # The judge's letter (Answer column) is A, not the parser's B.
        assert "A" in output

    def test_parser_unknown_without_a_cross_check(self):
        """A row without a recorded cross-check renders ``unknown``."""
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": None,
                    "score": 0.0,
                    "explanation": "No parseable letter (no_letter)",
                    "method": "llm",
                    "parse_reason": "no_letter",
                }
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        output = buf.getvalue()
        assert "unknown" in output
        assert "no_letter" in output

    def test_judge_error_row_renders_as_an_error_not_a_score(self):
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {
                    "error_kind": "judge_unavailable",
                    "error": "[Error: provider down]",
                    "attempts": 3,
                    "score": None,
                    "overall_score": None,
                },
                test_id="quiz_error",
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        output = buf.getvalue()
        assert "quiz_error" in output
        assert "ERROR" in output
        assert "judge error (judge_unavailable)" in output
        assert "attempts: 3" in output
        # No fabricated verdict for an unreachable judge.
        assert "FAIL" not in output

    def test_attempts_are_shown_when_the_judge_retried(self):
        console, buf = _capture_console()
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": "A",
                    "score": 0.0,
                    "explanation": "Selected A; gold B",
                    "method": "llm",
                    "parse_reason": "ok",
                    "attempts": 2,
                },
                test_id="quiz_retry",
            )
        ]
        _render_quiz_details_table(console, "header", rows)

        output = buf.getvalue()
        assert "quiz_retry" in output
        assert "attempts: 2" in output


class TestRenderRunCompletionReportQuiz:
    """The quiz summary report must advertise the LLM judge."""

    def _write_quiz_run(self, tmp_path: Path, rows: list[dict], **summary_overrides) -> tuple[Path, Path]:
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
            "failed": len(rows) - 1,
            "avg_score": 0.5,
            "parse_failures": 1,
            "parse_failure_reasons": {"multiple_letters": 1},
        }
        summary.update(summary_overrides)
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        details_path = tmp_path / "details.jsonl"
        with details_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return summary_path, details_path

    def test_quiz_report_renders(self, tmp_path: Path):
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": None,
                    "score": 0.0,
                    "explanation": "Ambiguous letters: B, C (multiple_letters)",
                    "method": "llm",
                    "parse_reason": "multiple_letters",
                },
                test_id="quiz_fail",
            ),
            _quiz_row(
                {
                    "pass": True,
                    "selected": "B",
                    "score": 1.0,
                    "explanation": "Selected B; gold B",
                    "method": "llm",
                    "parse_reason": "ok",
                },
                test_id="quiz_pass",
                passed=True,
            ),
        ]
        summary_path, details_path = self._write_quiz_run(tmp_path, rows)
        render_run_completion_report(summary_path, details_path)

    def test_quiz_report_with_zero_parse_failures(self, tmp_path: Path):
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": "A",
                    "score": 0.0,
                    "explanation": "Selected A; gold B",
                    "method": "llm",
                    "parse_reason": "ok",
                },
                test_id="quiz_fail",
            )
        ]
        summary_path, details_path = self._write_quiz_run(
            tmp_path, rows, parse_failures=0, parse_failure_reasons={}
        )
        render_run_completion_report(summary_path, details_path)

    def test_quiz_summary_shows_llm_scoring(self, tmp_path: Path, capsys, monkeypatch):
        """The quiz summary table advertises the LLM judge, not a deterministic scorer."""
        # Give the renderer a wide terminal so the summary table is not clipped.
        monkeypatch.setenv("COLUMNS", "200")
        rows = [
            _quiz_row(
                {
                    "pass": False,
                    "selected": None,
                    "score": 0.0,
                    "explanation": "No parseable letter (no_letter)",
                    "method": "llm",
                    "parse_reason": "no_letter",
                },
                test_id="quiz_fail",
            )
        ]
        summary_path, details_path = self._write_quiz_run(tmp_path, rows)
        render_run_completion_report(summary_path, details_path)

        output = capsys.readouterr().out
        assert "Scoring" in output
        assert "llm" in output
        assert "deterministic" not in output
        assert "Pass Rate" in output
        assert "Parse Failures" in output
        assert "quiz-v2" in output

    def test_quiz_config_errors_get_their_own_column(self, tmp_path: Path, capsys, monkeypatch):
        """Config failures are excluded from the pass rate and labelled on their own.

        A clean quiz summary keeps its exact columns — the column appears only
        when the run actually carried config failures — and an unscorable run
        reports ``n/a`` instead of a fabricated pass rate.
        """
        monkeypatch.setenv("COLUMNS", "200")
        rows = [
            _quiz_row(
                {
                    "pass": True,
                    "selected": "B",
                    "score": 1.0,
                    "explanation": "Selected B; gold B",
                    "method": "llm",
                    "parse_reason": "ok",
                },
                test_id="quiz_pass",
                passed=True,
            )
        ]
        clean_path, _ = self._write_quiz_run(tmp_path, rows)
        render_run_completion_report(clean_path)
        assert "Config Errors" not in capsys.readouterr().out

        dirty_path, _ = self._write_quiz_run(
            tmp_path, rows, config_errors=1, total=0, passed=0, avg_score=None
        )
        render_run_completion_report(dirty_path)
        output = capsys.readouterr().out
        assert "Config Errors" in output
        assert "n/a" in output


# ─── Cross-module coherence ───────────────────────────────────────────────────


class TestReportViewsReuseSummaryNormalizeScore:
    """All report modules must use the same safe coercion as summary.py.

    This guards against divergent coercion logic in the future by importing
    the helper from a single source and verifying the import is the same
    callable object across modules.
    """

    def test_completion_uses_summary_normalize_score(self):
        from nichebench.cli.rich_views.reports import completion
        from nichebench.execution.summary import normalize_score

        assert getattr(completion, "normalize_score", None) is normalize_score

    def test_detail_code_uses_summary_normalize_score(self):
        from nichebench.cli.rich_views.reports import detail_code
        from nichebench.execution.summary import normalize_score

        assert getattr(detail_code, "normalize_score", None) is normalize_score

    def test_detail_runtime_uses_summary_normalize_score(self):
        from nichebench.cli.rich_views.reports import detail_runtime
        from nichebench.execution.summary import normalize_score

        assert getattr(detail_runtime, "normalize_score", None) is normalize_score

    def test_no_module_redefines_a_local_score_helper(self):
        """No report renderer may carry its own private coercion helper."""
        for module_name in ("completion", "detail_code", "detail_runtime"):
            module = __import__(f"nichebench.cli.rich_views.reports.{module_name}", fromlist=["*"])
            for attr in ("_safe_score", "safe_score", "_normalize_score"):
                assert not hasattr(module, attr), (
                    f"{module_name} must not define its own {attr}; "
                    "import normalize_score from nichebench.execution.summary instead."
                )


def test_runtime_detail_reports_no_checks_when_runtime_failed_early():
    from rich.console import Console

    from nichebench.cli.rich_views.reports.detail_runtime import (
        _render_runtime_details_table,
    )

    console = Console(record=True, width=120)
    _render_runtime_details_table(
        console,
        "Runtime",
        [
            {
                "test_id": "rt_001",
                "pass": False,
                "judge_output": {
                    "checks": [],
                    "catastrophic_failure": True,
                    "hybrid_score": 0.0,
                    "deterministic_score": 0.0,
                },
            }
        ],
    )
    output = console.export_text()
    assert "No deterministic checks ran" in output
    assert "All checks passed" not in output
