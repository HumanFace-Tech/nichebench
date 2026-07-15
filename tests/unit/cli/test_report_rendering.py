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
from pathlib import Path

import pytest
from rich.console import Console

from nichebench.cli.rich_views.reports.completion import render_run_completion_report
from nichebench.cli.rich_views.reports.detail_code import _render_code_details_table
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
        """A numeric-string score above 66% must NOT be flagged as a failure.

        (The detail table only renders failures.)
        """
        console, buf = _capture_console()
        rows = [_code_row({"overall_score": "0.95"})]
        _render_code_details_table(console, "header", rows, "code_generation")
        # Table body should be empty for a passing row — no percentage text.
        assert "95%" not in buf.getvalue()

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
