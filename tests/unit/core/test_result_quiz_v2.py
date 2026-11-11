"""Unit tests for ``TestResult.to_dict()`` on the quiz path.

Quiz results carry a binary verdict recomputed from the judge's selected
letter.  The serialized row exposes that verdict as ``judge_score`` /
``final_score``, leaves ``deterministic_score`` empty, and keeps the parser
cross-check reason visible.  The code / bug / runtime serialization must
stay untouched.
"""

from typing import Any, Dict

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.result import TestResult


def _make_quiz_result(
    *,
    judge_output: Dict[str, Any],
    passed: bool,
    dataset_version: str = "quiz-v2",
) -> TestResult:
    """Build an LLM-judged quiz TestResult."""
    tc = TestCaseSpec(
        id="quiz_001",
        type="quiz",
        raw={"prompt": "question"},
        choices=["alpha", "beta", "gamma"],
        correct_choice="B",
        dataset_version=dataset_version,
    )
    result = TestResult(
        framework="drupal",
        category="quiz",
        test_case=tc,
        mut_model="openai/gpt-4o",
        judge_model="openai/gpt-5",
        judge_method="llm",
    )
    result.judge_output = judge_output
    result.passed = passed
    return result


def _make_classic_result(category: str, judge_output: Dict[str, Any]) -> TestResult:
    """Build a code/bug TestResult."""
    tc = TestCaseSpec(id="code_001", type=category, raw={"prompt": "task"})
    result = TestResult(
        framework="drupal",
        category=category,
        test_case=tc,
        mut_model="openai/gpt-4o",
        judge_model="openai/gpt-4o",
    )
    result.judge_output = judge_output
    return result


class TestQuizToDict:
    """Quiz serialization exposes the binary judge verdict."""

    def test_passing_quiz_row(self):
        result = _make_quiz_result(
            judge_output={
                "pass": True,
                "selected": "B",
                "score": 1.0,
                "explanation": "Selected B; gold B",
                "method": "llm",
                "parse_reason": "ok",
            },
            passed=True,
        )
        payload = result.to_dict()

        assert payload["deterministic_score"] is None
        assert payload["final_score"] == 1.0
        assert payload["judge_score"] == 1.0
        assert payload["judge_method"] == "llm"
        assert payload["parse_reason"] == "ok"
        assert payload["pass"] is True

    def test_failing_quiz_row_with_parse_failure(self):
        result = _make_quiz_result(
            judge_output={
                "pass": False,
                "selected": None,
                "score": 0.0,
                "explanation": "Ambiguous letters: B, C (multiple_letters)",
                "method": "llm",
                "parse_reason": "multiple_letters",
            },
            passed=False,
        )
        payload = result.to_dict()

        assert payload["deterministic_score"] is None
        assert payload["final_score"] == 0.0
        assert payload["judge_score"] == 0.0
        assert payload["parse_reason"] == "multiple_letters"
        assert payload["pass"] is False

    def test_dataset_version_is_included(self):
        result = _make_quiz_result(
            judge_output={"pass": True, "score": 1.0, "parse_reason": "ok"},
            passed=True,
            dataset_version="quiz-v2",
        )
        assert result.to_dict()["dataset_version"] == "quiz-v2"

    def test_quiz_row_without_score_falls_back_to_the_verdict(self):
        """An errored quiz row still carries a score."""
        result = _make_quiz_result(
            judge_output={"error": "MUT failed", "raw": "[Error: boom]"},
            passed=False,
        )
        payload = result.to_dict()

        assert payload["deterministic_score"] is None
        assert payload["final_score"] == 0.0
        assert payload["judge_score"] == 0.0
        assert payload["parse_reason"] is None

    def test_non_binary_score_does_not_override_the_verdict(self):
        """A stray non-binary score must not desync the row from avg_score."""
        result = _make_quiz_result(
            judge_output={"score": 0.5},
            passed=False,
        )
        payload = result.to_dict()

        assert payload["deterministic_score"] is None
        assert payload["final_score"] == 0.0
        assert payload["judge_score"] == 0.0

    def test_passing_verdict_with_non_binary_score_still_scores_one(self):
        result = _make_quiz_result(
            judge_output={"score": 0.5},
            passed=True,
        )
        payload = result.to_dict()

        assert payload["deterministic_score"] is None
        assert payload["final_score"] == 1.0
        assert payload["judge_score"] == 1.0

    def test_non_dict_judge_output_for_quiz(self):
        result = _make_quiz_result(judge_output="oops", passed=False)
        payload = result.to_dict()

        assert payload["deterministic_score"] is None
        assert payload["final_score"] == 0.0
        assert payload["judge_score"] == 0.0


class TestQuizErrorRowsCarryNullScores:
    """A judge or config failure must never serialize a fabricated zero."""

    @staticmethod
    def _make_error_result(error_kind: str, judge_output: Dict[str, Any]) -> TestResult:
        result = _make_quiz_result(judge_output=judge_output, passed=False)
        result.error_kind = error_kind
        result.judge_attempts = 3
        return result

    def test_judge_error_row_has_null_scores(self):
        result = self._make_error_result(
            "judge_unavailable",
            {
                "error_kind": "judge_unavailable",
                "error": "[Error: provider down]",
                "attempts": 3,
                "score": None,
                "overall_score": None,
            },
        )
        payload = result.to_dict()

        assert payload["judge_score"] is None
        assert payload["final_score"] is None
        assert payload["deterministic_score"] is None
        assert payload["pass"] is False
        assert payload["error_kind"] == "judge_unavailable"
        assert payload["judge_attempts"] == 3

    def test_config_error_row_has_null_scores(self):
        result = self._make_error_result(
            "config_error",
            {
                "pass": False,
                "selected": None,
                "score": 0.0,
                "explanation": "Quiz task is missing or invalid correct_choice",
                "method": "validation",
                "parse_reason": "invalid_gold",
                "error_kind": "config_error",
            },
        )
        payload = result.to_dict()

        assert payload["judge_score"] is None
        assert payload["final_score"] is None
        assert payload["deterministic_score"] is None
        assert payload["pass"] is False
        assert payload["error_kind"] == "config_error"
        # The validation verdict keeps its parse reason.
        assert payload["parse_reason"] == "invalid_gold"

    def test_non_dict_judge_output_with_error_kind_has_null_scores(self):
        result = _make_quiz_result(judge_output="oops", passed=False)
        result.error_kind = "judge_unavailable"
        payload = result.to_dict()

        assert payload["judge_score"] is None
        assert payload["final_score"] is None
        assert payload["deterministic_score"] is None
        assert payload["pass"] is False

    def test_successful_quiz_row_keeps_binary_scores(self):
        result = _make_quiz_result(
            judge_output={"pass": True, "selected": "B", "score": 1.0, "parse_reason": "ok"},
            passed=True,
        )
        payload = result.to_dict()

        assert payload["judge_score"] == 1.0
        assert payload["final_score"] == 1.0
        assert payload["deterministic_score"] is None


class TestClassicToDictUnchanged:
    """Code / bug / runtime serialization must not change."""

    def test_code_generation_scores(self):
        result = _make_classic_result(
            "code_generation",
            {"deterministic_score": 0.4, "judge_score": 0.8, "hybrid_score": 0.6},
        )
        payload = result.to_dict()

        assert payload["deterministic_score"] == 0.4
        assert payload["judge_score"] == 0.8
        assert payload["final_score"] == 0.6
        assert payload["judge_method"] == "llm"

    def test_bug_fixing_scores(self):
        result = _make_classic_result(
            "bug_fixing",
            {"deterministic_score": 0.1, "judge_score": 0.2, "hybrid_score": 0.15},
        )
        payload = result.to_dict()

        assert payload["deterministic_score"] == 0.1
        assert payload["judge_score"] == 0.2
        assert payload["final_score"] == 0.15

    def test_classic_row_without_scores(self):
        result = _make_classic_result("code_generation", {"criteria": []})
        payload = result.to_dict()

        assert payload["deterministic_score"] is None
        assert payload["judge_score"] is None
        assert payload["final_score"] is None
