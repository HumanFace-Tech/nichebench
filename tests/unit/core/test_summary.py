"""Unit tests for nichebench.execution.summary — score coercion and categorization."""

import json
import math
from pathlib import Path

import pytest

from nichebench.config.settings import settings
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
    """categorize_result should use ``result.passed`` for pass/fail and the
    score only for the display-only ``partial`` band (``score >= 0.33``)."""

    def test_string_score_above_pass_threshold(self):
        result = _make_result("code_generation", {"overall_score": "0.95"}, True)
        assert categorize_result(result) == "pass"

    def test_string_score_at_partial_threshold(self):
        # Partial requires the result to have failed the threshold.
        result = _make_result("code_generation", {"overall_score": "0.5"}, False)
        assert categorize_result(result) == "partial"

    def test_string_score_at_fail_threshold(self):
        result = _make_result("bug_fixing", {"overall_score": "0.2"}, False)
        assert categorize_result(result) == "fail"

    def test_float_score_unchanged_behavior(self):
        result = _make_result("code_generation", {"overall_score": 0.8}, True)
        assert categorize_result(result) == "pass"

    def test_int_score_unchanged_behavior(self):
        result = _make_result("bug_fixing", {"overall_score": 1}, True)
        assert categorize_result(result) == "pass"

    def test_missing_score_defaults_to_fail(self):
        result = _make_result("code_generation", {}, False)
        assert categorize_result(result) == "fail"

    def test_non_dict_judge_output_uses_passed_flag(self):
        result = _make_result("code_generation", "not a dict", True)
        assert categorize_result(result) == "pass"

    def test_non_numeric_string_score_falls_back_to_fail(self):
        result = _make_result("code_generation", {"overall_score": "garbage"}, False)
        assert categorize_result(result) == "fail"

    def test_non_finite_float_score_falls_back_to_fail(self):
        result = _make_result("code_generation", {"overall_score": float("nan")}, False)
        assert categorize_result(result) == "fail"

    def test_inf_float_score_falls_back_to_fail(self):
        result = _make_result("code_generation", {"overall_score": float("inf")}, False)
        assert categorize_result(result) == "fail"

    def test_non_finite_string_score_falls_back_to_fail(self):
        result = _make_result("code_generation", {"overall_score": "nan"}, False)
        assert categorize_result(result) == "fail"

    def test_category_not_code_gen_bug_fixing_uses_passed_flag(self):
        result = _make_result("quiz", {"overall_score": "0.95"}, False)
        assert categorize_result(result) == "fail"

    def test_068_not_passed_is_partial_not_pass(self):
        """The headline C3 case: 0.68 used to render as ``pass``.

        It never does — the pass flag is the verdict — and a failed verdict
        at that score is partial rather than fail: the band has no upper
        bound, because a result below a raised threshold still shows real
        progress.
        """
        result = _make_result("code_generation", {"overall_score": 0.68}, False)
        assert categorize_result(result) == "partial"

    def test_failed_verdict_above_the_old_upper_bound_is_partial(self):
        """0.67 used to fall out of ``[0.33, 0.66]`` and be counted as fail."""
        result = _make_result("code_generation", {"overall_score": 0.67}, False)
        assert categorize_result(result) == "partial"

    def test_score_at_the_partial_floor_is_partial(self):
        result = _make_result("code_generation", {"overall_score": 0.33}, False)
        assert categorize_result(result) == "partial"

    def test_score_below_the_partial_floor_is_fail(self):
        result = _make_result("code_generation", {"overall_score": 0.32}, False)
        assert categorize_result(result) == "fail"

    def test_070_passed_is_pass(self):
        result = _make_result("code_generation", {"overall_score": 0.70}, True)
        assert categorize_result(result) == "pass"

    def test_high_score_with_failed_verdict_is_never_pass(self):
        result = _make_result("bug_fixing", {"overall_score": 0.95}, False)
        assert categorize_result(result) == "partial"


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
        result = _make_result("code_generation", {"overall_score": value}, False)
        assert categorize_result(result) == "fail"

    @pytest.mark.parametrize("value", ["1.5", "-0.5"])
    def test_out_of_range_string_score_falls_back_to_fail(self, value: str):
        result = _make_result("bug_fixing", {"overall_score": value}, False)
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


# ─── Quiz (LLM judge) ────────────────────────────────────────────────────────


def _make_quiz_result(
    *,
    test_id: str = "quiz_001",
    passed: bool,
    score: float = 0.0,
    selected=None,
    parse_reason: str = "ok",
    dataset_version: str = "quiz-v2",
    judge_output=None,
) -> TestResult:
    """Build an LLM-judged quiz TestResult."""
    tc = TestCaseSpec(
        id=test_id,
        type="quiz",
        raw={"prompt": "test"},
        choices=["a", "b", "c", "d"],
        correct_choice="B",
        dataset_version=dataset_version,
    )
    result = TestResult(
        framework="test",
        category="quiz",
        test_case=tc,
        mut_model="test/model",
        judge_model="openai/gpt-5",
        judge_method="llm",
    )
    if judge_output is None:
        judge_output = {
            "pass": passed,
            "selected": selected,
            "score": score,
            "overall_score": score,
            "explanation": "Selected B; gold B" if passed else "No parseable letter",
            "method": "llm",
            "parse_reason": parse_reason,
            "parser": {
                "selected": selected,
                "parse_reason": parse_reason,
                "agrees": None if selected is None else True,
            },
        }
    result.judge_output = judge_output
    result.passed = passed
    return result


def _write_quiz_summary(results, tmp_path: Path) -> dict:
    """Run update_summary for a quiz run and return the written summary."""
    summary_path = tmp_path / "summary.json"
    update_summary(
        results=results,
        summary_path=summary_path,
        category="quiz",
        framework="test_framework",
        mut_model_str="test/mut",
        judge_model_str="test/judge",
        mut_runner_model_config={},
        judge_runner_model_config={},
        profile=None,
        eval_config={},
    )
    return json.loads(summary_path.read_text(encoding="utf-8"))


class TestCategorizeResultQuiz:
    """Quiz results are binary — pass or fail, never partial."""

    def test_passed_result_is_pass(self):
        result = _make_quiz_result(passed=True, score=1.0)
        assert categorize_result(result) == "pass"

    def test_failed_result_is_fail(self):
        result = _make_quiz_result(passed=False, score=0.0)
        assert categorize_result(result) == "fail"

    def test_quiz_is_never_partial(self):
        """Even a mid-range score stays binary for quiz."""
        result = _make_quiz_result(passed=False, score=0.5)
        assert categorize_result(result) == "fail"

    def test_judge_output_score_is_ignored_for_categorisation(self):
        """The verdict flag is authoritative, not a stray score field."""
        result = _make_quiz_result(
            passed=False,
            judge_output={"pass": False, "score": 0.9, "overall_score": 0.95},
        )
        assert categorize_result(result) == "fail"


class TestComputeAverageScoreQuiz:
    """The quiz average equals the pass rate."""

    def test_average_equals_pass_rate(self):
        results = [
            _make_quiz_result(test_id="q1", passed=True, score=1.0),
            _make_quiz_result(test_id="q2", passed=True, score=1.0),
            _make_quiz_result(test_id="q3", passed=False, score=0.0),
        ]
        assert compute_average_score(results, "quiz") == pytest.approx(2 / 3)

    def test_score_field_is_used_when_present(self):
        results = [
            _make_quiz_result(test_id="q1", passed=True, score=1.0),
            _make_quiz_result(test_id="q2", passed=False, score=0.0),
        ]
        assert compute_average_score(results, "quiz") == pytest.approx(0.5)

    def test_missing_score_falls_back_to_passed_flag(self):
        results = [
            _make_quiz_result(test_id="q1", passed=True, judge_output={"pass": True}),
            _make_quiz_result(test_id="q2", passed=False, judge_output={"pass": False}),
        ]
        assert compute_average_score(results, "quiz") == pytest.approx(0.5)

    def test_empty_results_returns_zero(self):
        assert compute_average_score([], "quiz") == 0.0


class TestUpdateSummaryQuiz:
    """summary.json for quiz must agree with the judge verdict."""

    def test_counts_and_pass_rate(self, tmp_path: Path):
        results = [
            _make_quiz_result(test_id="q1", passed=True, score=1.0, selected="B"),
            _make_quiz_result(test_id="q2", passed=True, score=1.0, selected="B"),
            _make_quiz_result(
                test_id="q3",
                passed=False,
                score=0.0,
                selected=None,
                parse_reason="multiple_letters",
            ),
        ]
        summary = _write_quiz_summary(results, tmp_path)

        assert summary["total"] == 3
        assert summary["passed"] == 2
        assert summary["failed"] == 1
        assert summary["partial"] == 0
        assert summary["avg_score"] == pytest.approx(2 / 3)

    def test_passed_count_matches_serialized_details(self, tmp_path: Path):
        results = [
            _make_quiz_result(test_id="q1", passed=True, score=1.0, selected="B"),
            _make_quiz_result(test_id="q2", passed=True, score=1.0, selected="B"),
            _make_quiz_result(
                test_id="q3",
                passed=False,
                score=0.0,
                selected=None,
                parse_reason="no_letter",
            ),
        ]
        summary = _write_quiz_summary(results, tmp_path)

        details = [r.to_dict() for r in results]
        pass_true = sum(1 for row in details if row["pass"] is True)
        assert summary["passed"] == pass_true
        assert summary["failed"] == len(details) - pass_true
        assert summary["avg_score"] == pytest.approx(pass_true / len(details))

    def test_final_score_is_populated_for_every_quiz_row(self, tmp_path: Path):
        results = [
            _make_quiz_result(test_id="q1", passed=True, score=1.0, selected="B"),
            _make_quiz_result(
                test_id="q2",
                passed=False,
                score=0.0,
                selected=None,
                parse_reason="empty",
            ),
        ]
        _write_quiz_summary(results, tmp_path)

        for row in (r.to_dict() for r in results):
            assert row["final_score"] is not None
            assert row["judge_score"] is not None
            assert row["deterministic_score"] is None
            assert row["judge_score"] == row["final_score"]

    def test_judge_method_is_llm(self, tmp_path: Path):
        results = [_make_quiz_result(passed=True, score=1.0)]
        summary = _write_quiz_summary(results, tmp_path)

        assert summary["judge_method"] == "llm"
        assert summary["judge"] == "test/judge"

    def test_config_carries_the_judge_model(self, tmp_path: Path):
        """Quiz is judged by an LLM — the configured judge travels with it."""
        results = [_make_quiz_result(passed=True, score=1.0)]
        summary_path = tmp_path / "summary.json"
        update_summary(
            results=results,
            summary_path=summary_path,
            category="quiz",
            framework="test_framework",
            mut_model_str="test/mut",
            judge_model_str="openai/gpt-5",
            mut_runner_model_config={"provider": "groq", "model": "test/mut"},
            judge_runner_model_config={"provider": "openai", "model": "gpt-5", "parameters": {}},
            profile=None,
            eval_config={},
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        assert summary["judge"] == "openai/gpt-5"
        assert summary["judge_method"] == "llm"
        assert summary["config"]["judge"] == {
            "provider": "openai",
            "model": "gpt-5",
            "parameters": {},
        }

    def test_code_generation_config_keeps_the_judge_model(self, tmp_path: Path):
        """The other categories keep their resolved judge configuration."""
        results = [_make_result("code_generation", {"overall_score": "0.95"}, True)]
        summary_path = tmp_path / "summary.json"
        update_summary(
            results=results,
            summary_path=summary_path,
            category="code_generation",
            framework="test_framework",
            mut_model_str="test/mut",
            judge_model_str="openai/gpt-5",
            mut_runner_model_config={"provider": "groq", "model": "test/mut"},
            judge_runner_model_config={"provider": "openai", "model": "gpt-5", "parameters": {}},
            profile=None,
            eval_config={},
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        assert summary["config"]["judge"] == {
            "provider": "openai",
            "model": "gpt-5",
            "parameters": {},
        }

    def test_dataset_version_is_propagated(self, tmp_path: Path):
        results = [_make_quiz_result(passed=True, score=1.0, dataset_version="quiz-v2")]
        summary = _write_quiz_summary(results, tmp_path)

        assert summary["dataset_version"] == "quiz-v2"

    def test_dataset_version_is_none_when_absent(self, tmp_path: Path):
        results = [_make_quiz_result(passed=True, score=1.0, dataset_version=None)]
        summary = _write_quiz_summary(results, tmp_path)

        assert summary["dataset_version"] is None

    def test_parse_failures_are_counted_and_broken_down(self, tmp_path: Path):
        results = [
            _make_quiz_result(test_id="q1", passed=True, score=1.0, parse_reason="ok"),
            _make_quiz_result(
                test_id="q2",
                passed=False,
                score=0.0,
                parse_reason="multiple_letters",
            ),
            _make_quiz_result(
                test_id="q3",
                passed=False,
                score=0.0,
                parse_reason="multiple_letters",
            ),
            _make_quiz_result(
                test_id="q4",
                passed=False,
                score=0.0,
                parse_reason="no_letter",
            ),
        ]
        summary = _write_quiz_summary(results, tmp_path)

        assert summary["parse_failures"] == 3
        assert summary["parse_failure_reasons"] == {"multiple_letters": 2, "no_letter": 1}

    def test_no_parse_failures_for_clean_run(self, tmp_path: Path):
        results = [
            _make_quiz_result(test_id="q1", passed=True, score=1.0, parse_reason="ok"),
            _make_quiz_result(test_id="q2", passed=False, score=0.0, parse_reason="ok"),
        ]
        summary = _write_quiz_summary(results, tmp_path)

        assert summary["parse_failures"] == 0
        assert summary["parse_failure_reasons"] == {}

    def test_code_generation_summary_keeps_its_shape(self, tmp_path: Path):
        """The new fields are additive — code/bug semantics stay the same."""
        results = [
            _make_result("code_generation", {"overall_score": "0.95"}, True),
            _make_result("code_generation", {"overall_score": "0.50"}, False),
            _make_result("code_generation", {"overall_score": "0.10"}, False),
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

        assert summary["judge"] == "test/judge"
        assert summary["judge_method"] == "llm"
        assert summary["total"] == 3
        assert summary["attempted_total"] == 3
        assert summary["judge_errors"] == 0
        assert summary["passed"] == 1
        assert summary["partial"] == 1
        assert summary["failed"] == 1
        assert summary["avg_score"] == pytest.approx((0.95 + 0.5 + 0.1) / 3)
        assert summary["parse_failures"] == 0
        assert summary["parse_failure_reasons"] == {}


class TestUpdateSummaryJudgeErrors:
    """Judge infrastructure failures are counted separately, never scored."""

    @staticmethod
    def _make_judge_error_result(test_id: str) -> TestResult:
        result = _make_result(
            "code_generation",
            {
                "error_kind": "judge_unavailable",
                "error": "[Error: provider down]",
                "attempts": 3,
                "judge_score": None,
                "final_score": None,
            },
            False,
        )
        result.test_case.id = test_id
        result.error_kind = "judge_unavailable"
        result.judge_attempts = 3
        return result

    @staticmethod
    def _write_summary(results, tmp_path: Path) -> dict:
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
        return json.loads(summary_path.read_text(encoding="utf-8"))

    def test_judge_error_is_excluded_from_scoring(self, tmp_path: Path):
        error_result = self._make_judge_error_result("bad_001")
        valid_result = _make_result("code_generation", {"overall_score": 0.8}, True)

        summary = self._write_summary([error_result, valid_result], tmp_path)

        assert summary["judge_errors"] == 1
        assert summary["attempted_total"] == 2
        assert summary["total"] == 1
        assert summary["passed"] == 1
        assert summary["failed"] == 0
        # Average is computed over the valid result only.
        assert summary["avg_score"] == pytest.approx(0.8)

    def test_all_judge_errors_leave_avg_score_none(self, tmp_path: Path):
        results = [self._make_judge_error_result("bad_001"), self._make_judge_error_result("bad_002")]

        summary = self._write_summary(results, tmp_path)

        assert summary["judge_errors"] == 2
        assert summary["attempted_total"] == 2
        assert summary["total"] == 0
        assert summary["passed"] == 0
        assert summary["partial"] == 0
        assert summary["failed"] == 0
        assert summary["avg_score"] is None


class TestUpdateSummaryConfigErrors:
    """Harness/configuration failures are counted separately, never scored.

    A ``config_error`` row (an exception that escaped the runners, an unknown
    category) says nothing about the model, so it must not drag the average
    — and it is labelled ``config_errors``, never folded into
    ``judge_errors``.
    """

    @staticmethod
    def _make_config_error_result(category: str = "code_generation", *, test_id: str = "cfg_001") -> TestResult:
        result = _make_result(
            category,
            {"error": "[Error: harness blew up]", "error_kind": "config_error"},
            False,
        )
        result.test_case.id = test_id
        result.error_kind = "config_error"
        return result

    @staticmethod
    def _write_summary(results, tmp_path: Path, category: str = "code_generation") -> dict:
        summary_path = tmp_path / "summary.json"
        update_summary(
            results=results,
            summary_path=summary_path,
            category=category,
            framework="test_framework",
            mut_model_str="test/mut",
            judge_model_str="test/judge",
            mut_runner_model_config={},
            judge_runner_model_config={},
            profile=None,
            eval_config={},
        )
        return json.loads(summary_path.read_text(encoding="utf-8"))

    def test_config_error_is_excluded_from_scoring(self, tmp_path: Path):
        config_result = self._make_config_error_result()
        valid_result = _make_result("code_generation", {"overall_score": 0.8}, True)

        summary = self._write_summary([config_result, valid_result], tmp_path)

        assert summary["config_errors"] == 1
        # Labelled separately: a config failure is not a judge failure.
        assert summary["judge_errors"] == 0
        assert summary["attempted_total"] == 2
        assert summary["total"] == 1
        assert summary["passed"] == 1
        assert summary["failed"] == 0
        assert summary["avg_score"] == pytest.approx(0.8)

    def test_all_config_errors_leave_avg_score_none(self, tmp_path: Path):
        results = [self._make_config_error_result(test_id="cfg_001"), self._make_config_error_result(test_id="cfg_002")]

        summary = self._write_summary(results, tmp_path)

        assert summary["config_errors"] == 2
        assert summary["judge_errors"] == 0
        assert summary["attempted_total"] == 2
        assert summary["total"] == 0
        assert summary["passed"] == 0
        assert summary["failed"] == 0
        assert summary["avg_score"] is None

    def test_quiz_pass_rate_ignores_config_errors(self, tmp_path: Path):
        """The quiz average stays the pass rate over the *scored* rows."""
        config_result = _make_quiz_result(test_id="q3", passed=False, score=0.0)
        config_result.judge_output = {"error": "[Error: harness blew up]", "error_kind": "config_error"}
        config_result.error_kind = "config_error"
        results = [
            _make_quiz_result(test_id="q1", passed=True, score=1.0),
            _make_quiz_result(test_id="q2", passed=False, score=0.0),
            config_result,
        ]

        summary = self._write_summary(results, tmp_path, category="quiz")

        assert summary["config_errors"] == 1
        assert summary["judge_errors"] == 0
        assert summary["attempted_total"] == 3
        assert summary["total"] == 2
        assert summary["passed"] == 1
        assert summary["failed"] == 1
        assert summary["avg_score"] == pytest.approx(0.5)

    def test_runtime_summaries_are_untouched(self, tmp_path: Path):
        """Runtime rows carry no ``error_kind``, so their scoring is unchanged."""
        results = [_make_result("runtime", {"hybrid_score": 0.8}, True)]

        summary = self._write_summary(results, tmp_path, category="runtime")

        assert summary["config_errors"] == 0
        assert summary["judge_errors"] == 0
        assert summary["total"] == 1
        assert summary["avg_score"] == pytest.approx(0.8)


class TestSummaryRecordsTheEffectiveThreshold:
    """``config.evaluation.judge_threshold`` is what a later report renders with.

    A report is rendered long after the run, so the bar the run used has to
    travel inside ``summary.json`` instead of being re-read from a possibly
    changed environment.
    """

    @staticmethod
    def _write_summary(category: str, eval_config: dict, tmp_path: Path) -> dict:
        summary_path = tmp_path / "summary.json"
        update_summary(
            results=[_make_result(category, {"overall_score": 0.8}, True)],
            summary_path=summary_path,
            category=category,
            framework="test_framework",
            mut_model_str="test/mut",
            judge_model_str="test/judge",
            mut_runner_model_config={},
            judge_runner_model_config={},
            profile=None,
            eval_config=eval_config,
        )
        return json.loads(summary_path.read_text(encoding="utf-8"))

    def test_the_setting_is_recorded_when_the_config_does_not_set_it(self, tmp_path: Path):
        summary = self._write_summary("code_generation", {}, tmp_path)

        assert summary["config"]["evaluation"]["judge_threshold"] == settings.judge_threshold

    def test_a_configured_threshold_is_recorded_verbatim(self, tmp_path: Path):
        eval_config = {"judge_threshold": 0.9}

        summary = self._write_summary("bug_fixing", eval_config, tmp_path)

        assert summary["config"]["evaluation"]["judge_threshold"] == 0.9
        # The caller's dict is copied, never mutated.
        assert eval_config == {"judge_threshold": 0.9}

    def test_quiz_advertises_no_judge_threshold(self, tmp_path: Path):
        """Quiz verdicts are exact-match — no judge pass bar applies."""
        summary = _write_quiz_summary([_make_quiz_result(passed=True, score=1.0)], tmp_path)

        assert "judge_threshold" not in summary["config"]["evaluation"]
