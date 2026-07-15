"""Unit tests for nichebench.execution.summary — score coercion and categorization."""

import json
import math
from pathlib import Path

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.result import TestResult
from nichebench.execution.summary import (
    _extract_runtime_score,
    categorize_result,
    compute_average_score,
    normalize_score,
    update_summary,
)


def _make_result(category: str, judge_output: dict, passed: bool) -> TestResult:
    """Build a minimal TestResult for testing."""
    tc = TestCaseSpec(id="test_001", type=category, raw={"prompt": "test"})
    result = TestResult(
        framework="test",
        category=category,
        test_case=tc,
        mut_model="test/model",
        judge_model="test/judge",
    )
    result.judge_output = judge_output
    result.passed = passed
    return result


class TestNormalizeScore:
    """normalize_score should coerce various inputs to finite floats in [0, 1]."""

    def test_float_pass_through(self):
        assert normalize_score(0.95) == pytest.approx(0.95)

    def test_int_pass_through(self):
        assert normalize_score(1) == pytest.approx(1.0)

    def test_numeric_string_coerced(self):
        assert normalize_score("0.95") == pytest.approx(0.95)
        assert normalize_score("1.0") == pytest.approx(1.0)
        assert normalize_score("0") == pytest.approx(0.0)

    def test_bool_returns_default(self):
        assert normalize_score(True) == 0.0
        assert normalize_score(False) == 0.0

    def test_non_numeric_string_returns_default(self):
        assert normalize_score("invalid") == 0.0
        assert normalize_score("") == 0.0

    def test_none_returns_default(self):
        assert normalize_score(None) == 0.0

    def test_custom_default(self):
        assert normalize_score("invalid", default=0.5) == 0.5
        assert normalize_score(None, default=0.5) == 0.5

    def test_non_finite_float_returns_default(self):
        assert normalize_score(float("nan")) == 0.0
        assert normalize_score(float("inf")) == 0.0
        assert normalize_score(float("-inf")) == 0.0

    def test_non_finite_string_returns_default(self):
        assert normalize_score("nan") == 0.0
        assert normalize_score("inf") == 0.0
        assert normalize_score("-inf") == 0.0

    def test_non_finite_with_custom_default(self):
        assert normalize_score(float("nan"), default=0.5) == 0.5
        assert normalize_score("inf", default=0.5) == 0.5


class TestCategorizeResultCodeGenBugFix:
    """categorize_result should handle numeric strings in judge_output for code_generation / bug_fixing."""

    def test_string_score_above_pass_threshold(self):
        result = _make_result("code_generation", {"overall_score": "0.95"}, True)
        assert categorize_result(result) == "pass"

    def test_string_score_at_partial_threshold(self):
        result = _make_result("code_generation", {"overall_score": "0.5"}, True)
        assert categorize_result(result) == "partial"

    def test_string_score_at_fail_threshold(self):
        result = _make_result("bug_fixing", {"overall_score": "0.2"}, True)
        assert categorize_result(result) == "fail"

    def test_float_score_unchanged_behavior(self):
        result = _make_result("code_generation", {"overall_score": 0.8}, True)
        assert categorize_result(result) == "pass"

    def test_int_score_unchanged_behavior(self):
        result = _make_result("bug_fixing", {"overall_score": 1}, True)
        assert categorize_result(result) == "pass"

    def test_missing_score_defaults_to_fail(self):
        result = _make_result("code_generation", {}, True)
        assert categorize_result(result) == "fail"

    def test_non_dict_judge_output_uses_passed_flag(self):
        result = _make_result("code_generation", "not a dict", True)
        assert categorize_result(result) == "pass"

    def test_non_numeric_string_score_falls_back_to_fail(self):
        result = _make_result("code_generation", {"overall_score": "garbage"}, True)
        assert categorize_result(result) == "fail"

    def test_non_finite_float_score_falls_back_to_fail(self):
        result = _make_result("code_generation", {"overall_score": float("nan")}, True)
        assert categorize_result(result) == "fail"

    def test_inf_float_score_falls_back_to_fail(self):
        result = _make_result("code_generation", {"overall_score": float("inf")}, True)
        assert categorize_result(result) == "fail"

    def test_non_finite_string_score_falls_back_to_fail(self):
        result = _make_result("code_generation", {"overall_score": "nan"}, True)
        assert categorize_result(result) == "fail"

    def test_category_not_code_gen_bug_fixing_uses_passed_flag(self):
        result = _make_result("quiz", {"overall_score": "0.95"}, False)
        assert categorize_result(result) == "fail"


class TestComputeAverageScore:
    """compute_average_score should handle numeric strings in judge_output."""

    def test_string_score_included_in_average(self):
        results = [
            _make_result("code_generation", {"overall_score": "0.8"}, True),
            _make_result("code_generation", {"overall_score": "0.6"}, True),
        ]
        avg = compute_average_score(results, "code_generation")
        assert avg == pytest.approx(0.7)

    def test_mixed_float_and_string_scores(self):
        results = [
            _make_result("code_generation", {"overall_score": 0.8}, True),
            _make_result("code_generation", {"overall_score": "0.4"}, True),
        ]
        avg = compute_average_score(results, "code_generation")
        assert avg == pytest.approx(0.6)

    def test_non_numeric_string_score_falls_back_to_zero(self):
        results = [
            _make_result("code_generation", {"overall_score": "0.8"}, True),
            _make_result("code_generation", {"overall_score": "invalid"}, True),
        ]
        avg = compute_average_score(results, "code_generation")
        assert avg == pytest.approx(0.4)

    def test_non_finite_float_score_falls_back_to_zero(self):
        results = [
            _make_result("code_generation", {"overall_score": "0.8"}, True),
            _make_result("code_generation", {"overall_score": float("nan")}, True),
        ]
        avg = compute_average_score(results, "code_generation")
        assert avg == pytest.approx(0.4)

    def test_missing_score_treated_as_zero(self):
        results = [
            _make_result("code_generation", {"overall_score": "0.8"}, True),
            _make_result("code_generation", {}, True),
        ]
        avg = compute_average_score(results, "code_generation")
        assert avg == pytest.approx(0.4)

    def test_empty_results_returns_zero(self):
        avg = compute_average_score([], "code_generation")
        assert avg == 0.0

    def test_non_dict_judge_output_uses_passed_flag(self):
        results = [
            _make_result("code_generation", "not a dict", True),
            _make_result("code_generation", "not a dict", False),
        ]
        avg = compute_average_score(results, "code_generation")
        assert avg == pytest.approx(0.5)


class TestUpdateSummarySerialization:
    """update_summary should produce a finite, safe summary.json from non-finite judge output."""

    def test_non_finite_scores_produce_finite_avg_score(self, tmp_path: Path):
        """Non-finite judge scores (nan/inf/-inf as float or string) must not appear in summary.json."""
        results = [
            _make_result("code_generation", {"overall_score": float("nan")}, False),
            _make_result("code_generation", {"overall_score": float("inf")}, False),
            _make_result("code_generation", {"overall_score": float("-inf")}, False),
            _make_result("code_generation", {"overall_score": "nan"}, False),
            _make_result("code_generation", {"overall_score": "inf"}, False),
            _make_result("code_generation", {"overall_score": "-inf"}, False),
        ]

        summary_path = tmp_path / "summary.json"
        update_summary(
            results=results,
            summary_path=summary_path,
            category="code_generation",
            framework="test_framework",
            mut_model_str="test/mut",
            judge_model_str="test/judge",
            mut_runner_model_config={},
            judge_runner_model_config={},
            profile=None,
            eval_config={},
        )

        assert summary_path.exists(), "summary.json was not written"

        raw = summary_path.read_text(encoding="utf-8")
        summary = json.loads(raw)

        # avg_score must be finite and safe (all non-finite inputs fell back to 0.0)
        assert math.isfinite(summary["avg_score"]), f"avg_score must be finite, got {summary['avg_score']}"
        assert 0.0 <= summary["avg_score"] <= 1.0, f"avg_score must be in [0,1], got {summary['avg_score']}"

        # No non-finite values anywhere in the JSON string
        for value in json.loads(raw).values():
            if isinstance(value, float):
                assert math.isfinite(value), f"non-finite float found in summary: {value}"

    def test_mixed_finite_and_non_finite_scores(self, tmp_path: Path):
        """A mix of finite and non-finite scores should average only the finite ones."""
        results = [
            _make_result("code_generation", {"overall_score": "0.8"}, True),
            _make_result("code_generation", {"overall_score": float("nan")}, False),
            _make_result("code_generation", {"overall_score": "inf"}, False),
            _make_result("code_generation", {"overall_score": "0.4"}, True),
        ]

        summary_path = tmp_path / "summary.json"
        update_summary(
            results=results,
            summary_path=summary_path,
            category="code_generation",
            framework="test_framework",
            mut_model_str="test/mut",
            judge_model_str="test/judge",
            mut_runner_model_config={},
            judge_runner_model_config={},
            profile=None,
            eval_config={},
        )

        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        # Only "0.8" and "0.4" are finite → avg = (0.8 + 0.4) / 4 = 0.3
        assert math.isfinite(summary["avg_score"]), f"avg_score not finite: {summary['avg_score']}"
        assert summary["avg_score"] == pytest.approx(0.3)

        # Verify no non-finite values in the serialized JSON
        raw = summary_path.read_text(encoding="utf-8")
        assert "nan" not in raw.lower() or ".nan" in raw.lower()  # allow ".nan" as valid float format
        # More precise: check that "Infinity" or "Inf" doesn't appear
        assert "Infinity" not in raw and "Inf" not in raw


class TestNormalizeScoreOutOfRange:
    """normalize_score should reject values outside [0, 1]."""

    @pytest.mark.parametrize("value", [1.5, 2.0, -0.5, -1.0, 100.0])
    def test_float_out_of_range_returns_default(self, value: float):
        assert normalize_score(value) == 0.0

    @pytest.mark.parametrize("value", ["1.5", "2.0", "-0.5", "-1.0", "100.0"])
    def test_string_out_of_range_returns_default(self, value: str):
        assert normalize_score(value) == 0.0

    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
    def test_boundary_values_accepted(self, value: float):
        assert normalize_score(value) == pytest.approx(value)

    def test_out_of_range_with_custom_default(self):
        assert normalize_score(1.5, default=0.5) == 0.5
        assert normalize_score(-0.5, default=0.5) == 0.5
        assert normalize_score("1.5", default=0.5) == 0.5


class TestExtractRuntimeScoreNonFiniteOutOfRange:
    """_extract_runtime_score should reject non-finite and out-of-range values."""

    def test_non_finite_float_score_rejected(self):
        for key in ("hybrid_score", "final_score", "judge_score", "deterministic_score"):
            result = _make_result("runtime", {key: float("nan")}, True)
            assert _extract_runtime_score(result) == 0.0  # rejected as 0.0, not boolean fallback

            result = _make_result("runtime", {key: float("inf")}, True)
            assert _extract_runtime_score(result) == 0.0

            result = _make_result("runtime", {key: float("-inf")}, True)
            assert _extract_runtime_score(result) == 0.0

    def test_out_of_range_float_score_rejected(self):
        for key in ("hybrid_score", "final_score", "judge_score", "deterministic_score"):
            result = _make_result("runtime", {key: 1.5}, True)
            assert _extract_runtime_score(result) == 0.0  # rejected as 0.0, not boolean fallback

            result = _make_result("runtime", {key: -0.5}, False)
            assert _extract_runtime_score(result) == 0.0

    def test_non_finite_string_score_rejected(self):
        for key in ("hybrid_score", "final_score", "judge_score", "deterministic_score"):
            result = _make_result("runtime", {key: "nan"}, True)
            assert _extract_runtime_score(result) == 0.0

            result = _make_result("runtime", {key: "inf"}, True)
            assert _extract_runtime_score(result) == 0.0

    def test_out_of_range_string_score_rejected(self):
        for key in ("hybrid_score", "final_score", "judge_score", "deterministic_score"):
            result = _make_result("runtime", {key: "1.5"}, True)
            assert _extract_runtime_score(result) == 0.0

            result = _make_result("runtime", {key: "-0.5"}, False)
            assert _extract_runtime_score(result) == 0.0

    def test_valid_score_in_range_returned(self):
        for key in ("hybrid_score", "final_score", "judge_score", "deterministic_score"):
            result = _make_result("runtime", {key: 0.8}, False)
            assert _extract_runtime_score(result) == pytest.approx(0.8)

            result = _make_result("runtime", {key: "0.5"}, True)
            assert _extract_runtime_score(result) == pytest.approx(0.5)

    def test_no_valid_score_falls_back_to_passed_flag(self):
        result = _make_result("runtime", {}, False)
        assert _extract_runtime_score(result) == 0.0

        result = _make_result("runtime", {"hybrid_score": "garbage"}, True)
        assert _extract_runtime_score(result) == 1.0


class TestExtractRuntimeScoreFallThrough:
    """Invalid score-like values in a higher-priority field MUST fall through
    to the next lower-priority valid score (regression for review finding).

    Boolean ``passed`` fallback applies only when no score-like value is
    present at all.
    """

    @pytest.mark.parametrize("value", [True, False])
    def test_boolean_hybrid_suppresses_passed_fallback(self, value: bool):
        result = _make_result("runtime", {"hybrid_score": value}, True)

        assert _extract_runtime_score(result) == 0.0

    @pytest.mark.parametrize("value", [True, False])
    def test_boolean_hybrid_falls_through_to_valid_lower_priority_score(self, value: bool):
        result = _make_result("runtime", {"hybrid_score": value, "final_score": 0.8}, False)

        assert _extract_runtime_score(result) == pytest.approx(0.8)

    def test_non_finite_string_hybrid_falls_through_to_final(self):
        """hybrid_score='nan' must NOT short-circuit; final_score=0.8 wins."""
        result = _make_result("runtime", {"hybrid_score": "nan", "final_score": 0.8}, False)
        assert _extract_runtime_score(result) == pytest.approx(0.8)

    def test_non_finite_string_hybrid_falls_through_to_judge(self):
        """hybrid_score='nan' + final_score='inf' falls through to judge_score."""
        result = _make_result(
            "runtime",
            {"hybrid_score": "nan", "final_score": "inf", "judge_score": 0.5},
            False,
        )
        assert _extract_runtime_score(result) == pytest.approx(0.5)

    def test_out_of_range_string_hybrid_falls_through(self):
        """hybrid_score='1.5' falls through to final_score=0.6."""
        result = _make_result("runtime", {"hybrid_score": "1.5", "final_score": 0.6}, False)
        assert _extract_runtime_score(result) == pytest.approx(0.6)

    def test_out_of_range_negative_string_hybrid_falls_through(self):
        """hybrid_score='-0.5' falls through to final_score=0.7."""
        result = _make_result("runtime", {"hybrid_score": "-0.5", "final_score": 0.7}, False)
        assert _extract_runtime_score(result) == pytest.approx(0.7)

    def test_non_finite_string_hybrid_falls_through_to_string_final(self):
        """String fall-through also works between string scores."""
        result = _make_result(
            "runtime",
            {"hybrid_score": "inf", "final_score": "0.9"},
            False,
        )
        assert _extract_runtime_score(result) == pytest.approx(0.9)

    def test_chain_of_invalid_strings_falls_through(self):
        """Multiple invalid string keys fall through to the first valid one."""
        result = _make_result(
            "runtime",
            {
                "hybrid_score": "nan",
                "final_score": "inf",
                "judge_score": "garbage",
                "deterministic_score": 0.4,
            },
            False,
        )
        assert _extract_runtime_score(result) == pytest.approx(0.4)

    def test_chain_of_invalid_floats_falls_through(self):
        """All priority float keys invalid → return 0.0 (no boolean fallback)."""
        result = _make_result(
            "runtime",
            {
                "hybrid_score": float("nan"),
                "final_score": float("inf"),
                "judge_score": 1.5,
                "deterministic_score": -0.3,
                # Out-of-priority key — must be ignored.
                "extra_score": 0.9,
            },
            True,  # passed=True but irrelevant — numeric values were all invalid.
        )
        # Numeric-shaped values existed somewhere in the priority chain,
        # so we must NOT fall back to the boolean passed flag. Return 0.0.
        assert _extract_runtime_score(result) == 0.0

    def test_mixed_float_and_string_invalid_keys_fall_through(self):
        """Mixed float/string invalid keys both fall through to a valid one."""
        result = _make_result(
            "runtime",
            {
                "hybrid_score": "nan",
                "final_score": float("inf"),
                "judge_score": "1.5",
                "deterministic_score": "0.8",
            },
            False,
        )
        assert _extract_runtime_score(result) == pytest.approx(0.8)

    def test_fall_through_to_passed_flag_only_when_no_numeric_at_all(self):
        """When every priority key is a non-numeric string, boolean fallback applies."""
        result = _make_result(
            "runtime",
            {
                "hybrid_score": "garbage",
                "final_score": "garbage",
                "judge_score": "garbage",
                "deterministic_score": "garbage",
            },
            True,
        )
        # No numeric at all → boolean fallback (passed=True → 1.0).
        assert _extract_runtime_score(result) == 1.0

    def test_fall_through_returns_zero_when_numeric_but_all_invalid(self):
        """When a numeric-looking-but-invalid value is present anywhere, return 0.0.

        Prevents boolean fallback from masking data hygiene problems.
        """
        result = _make_result(
            "runtime",
            {
                "hybrid_score": "nan",
                "final_score": "inf",
                "judge_score": "1.5",
                "deterministic_score": "-0.3",
            },
            True,  # passed=True, but we have numeric-looking invalid values
        )
        assert _extract_runtime_score(result) == 0.0

    def test_priority_preserved_when_top_key_valid(self):
        """When the top-priority key is valid, lower keys are ignored."""
        result = _make_result(
            "runtime",
            {
                "hybrid_score": 0.3,
                "final_score": 0.9,
                "judge_score": 0.5,
                "deterministic_score": 0.2,
            },
            False,
        )
        assert _extract_runtime_score(result) == pytest.approx(0.3)


class TestCategorizeResultOutOfRange:
    """categorize_result should handle out-of-range scores for code_generation / bug_fixing."""

    @pytest.mark.parametrize("value", [1.5, -0.5, 2.0])
    def test_out_of_range_float_score_falls_back_to_fail(self, value: float):
        result = _make_result("code_generation", {"overall_score": value}, True)
        assert categorize_result(result) == "fail"

    @pytest.mark.parametrize("value", ["1.5", "-0.5"])
    def test_out_of_range_string_score_falls_back_to_fail(self, value: str):
        result = _make_result("bug_fixing", {"overall_score": value}, True)
        assert categorize_result(result) == "fail"


class TestComputeAverageScoreOutOfRange:
    """compute_average_score should treat out-of-range values as 0."""

    def test_out_of_range_float_score_classic(self):
        results = [
            _make_result("code_generation", {"overall_score": 0.8}, True),
            _make_result("code_generation", {"overall_score": 1.5}, True),
            _make_result("code_generation", {"overall_score": -0.5}, True),
        ]
        avg = compute_average_score(results, "code_generation")
        assert avg == pytest.approx(0.8 / 3)  # only 0.8 counts

    def test_out_of_range_string_score_classic(self):
        results = [
            _make_result("code_generation", {"overall_score": "0.8"}, True),
            _make_result("code_generation", {"overall_score": "1.5"}, True),
        ]
        avg = compute_average_score(results, "code_generation")
        assert avg == pytest.approx(0.4)  # only "0.8" counts

    def test_out_of_range_runtime(self):
        results = [
            _make_result("runtime", {"hybrid_score": 0.8}, True),
            _make_result("runtime", {"hybrid_score": 1.5}, True),
            _make_result("runtime", {"hybrid_score": -0.5}, False),
        ]
        avg = compute_average_score(results, "runtime")
        assert avg == pytest.approx(0.8 / 3)

    def test_non_finite_runtime(self):
        results = [
            _make_result("runtime", {"hybrid_score": 0.8}, True),
            _make_result("runtime", {"hybrid_score": float("nan")}, True),
            _make_result("runtime", {"hybrid_score": "inf"}, False),
        ]
        avg = compute_average_score(results, "runtime")
        assert avg == pytest.approx(0.8 / 3)


class TestUpdateSummaryRuntimeNonFiniteOutOfRange:
    """update_summary should produce finite, in-range avg_score for runtime with bad scores."""

    def test_runtime_non_finite_out_of_range_produces_safe_avg(self, tmp_path: Path):
        results = [
            _make_result("runtime", {"hybrid_score": float("nan")}, False),
            _make_result("runtime", {"hybrid_score": float("inf")}, False),
            _make_result("runtime", {"hybrid_score": 1.5}, False),
            _make_result("runtime", {"hybrid_score": -0.5}, False),
        ]

        summary_path = tmp_path / "summary.json"
        update_summary(
            results=results,
            summary_path=summary_path,
            category="runtime",
            framework="test_framework",
            mut_model_str="test/mut",
            judge_model_str="test/judge",
            mut_runner_model_config={},
            judge_runner_model_config={},
            profile=None,
            eval_config={},
        )

        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        assert math.isfinite(summary["avg_score"]), f"avg_score must be finite, got {summary['avg_score']}"
        assert 0.0 <= summary["avg_score"] <= 1.0, f"avg_score must be in [0,1], got {summary['avg_score']}"

        # all results fell back to passed=False → 0.0, avg should be 0.0
        assert summary["avg_score"] == pytest.approx(0.0)

    def test_runtime_mixed_valid_and_invalid_scores(self, tmp_path: Path):
        results = [
            _make_result("runtime", {"hybrid_score": 0.8}, True),
            _make_result("runtime", {"hybrid_score": float("nan")}, False),
            _make_result("runtime", {"hybrid_score": 1.5}, False),
            _make_result("runtime", {"hybrid_score": "0.4"}, True),
        ]

        summary_path = tmp_path / "summary.json"
        update_summary(
            results=results,
            summary_path=summary_path,
            category="runtime",
            framework="test_framework",
            mut_model_str="test/mut",
            judge_model_str="test/judge",
            mut_runner_model_config={},
            judge_runner_model_config={},
            profile=None,
            eval_config={},
        )

        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        # only 0.8 and 0.4 are valid → avg = (0.8 + 0.4) / 4 = 0.3
        assert math.isfinite(summary["avg_score"])
        assert 0.0 <= summary["avg_score"] <= 1.0
        assert summary["avg_score"] == pytest.approx(0.3)
