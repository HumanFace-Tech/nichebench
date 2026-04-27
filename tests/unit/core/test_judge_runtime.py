"""Unit tests for runtime judge path in JudgeRunner.evaluate_test()."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.executor import (
    JudgeRunner,
    _build_runtime_artifact_summary,
    _build_runtime_task_description,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_judge_runner(judge_output: dict[str, Any]) -> JudgeRunner:
    """Return a JudgeRunner whose underlying judge always returns judge_output."""
    runner = JudgeRunner.__new__(JudgeRunner)
    runner.model_str = "openai/gpt-4o"
    runner.model_config = {}
    mock_client = MagicMock()
    from nichebench.providers.litellm_judge import LiteLLMJudge

    mock_judge = MagicMock(spec=LiteLLMJudge)
    mock_judge.score_runtime.return_value = judge_output
    runner.client = mock_client
    runner.judge = mock_judge
    return runner


def _make_test_case(raw: dict[str, Any]) -> TestCaseSpec:
    tc = MagicMock(spec=TestCaseSpec)
    tc.raw = raw
    tc.correct_choice = None
    tc.checklist = []
    tc.context = None
    tc.summary = None
    return tc


# ---------------------------------------------------------------------------
# Tests for _build_runtime_task_description
# ---------------------------------------------------------------------------


def test_build_task_description_with_full_manifest():
    raw = {
        "title": "Build a wizard",
        "description_structured": {
            "background": "Need a wizard for applications.",
            "acceptance_criteria": ["Step 1 works", "Step 2 works"],
        },
    }
    result = _build_runtime_task_description(raw)
    assert "Build a wizard" in result
    assert "Need a wizard for applications." in result
    assert "Step 1 works" in result


def test_build_task_description_empty():
    result = _build_runtime_task_description({})
    assert result == "(no task description available)"


# ---------------------------------------------------------------------------
# Tests for _build_runtime_artifact_summary
# ---------------------------------------------------------------------------


def test_build_artifact_summary_with_diff_and_checks():
    artifacts = {
        "final.diff": "+++ added line\n--- removed line",
        "checks.json": {
            "deterministic": [
                {"passed": True, "name": "module_exists", "message": "found"},
                {"passed": False, "name": "routing_yml", "message": "missing"},
            ]
        },
    }
    result = _build_runtime_artifact_summary(artifacts)
    assert "GIT DIFF" in result
    assert "PASS" in result
    assert "FAIL" in result
    assert "module_exists" in result


def test_build_artifact_summary_truncates_long_diff():
    big_diff = "x" * 20000
    result = _build_runtime_artifact_summary({"final.diff": big_diff})
    assert "truncated" in result
    assert len(result) < 15000  # Sanity check that truncation worked


def test_build_artifact_summary_empty():
    result = _build_runtime_artifact_summary({})
    assert result == "(no artifacts available)"


# ---------------------------------------------------------------------------
# Tests for JudgeRunner.evaluate_test with category="runtime"
# ---------------------------------------------------------------------------


def test_evaluate_test_runtime_calls_score_runtime():
    """evaluate_test('runtime') must call score_runtime and return overall_score."""
    checklist = [{"id": "x", "question": "Q?", "weight": 1.0}]
    judge_output = {
        "overall_score": 0.75,
        "criteria": [{"criterion_id": "x", "pass": True, "explanation": "ok"}],
        "summary": "Good work",
        "raw": "{}",
    }
    runner = _make_judge_runner(judge_output)
    tc = _make_test_case({"llm_judge": {"checklist": checklist, "model_role": "Be strict."}})

    out, passed = runner.evaluate_test(
        tc, "runtime", "user prompt", "agent output", None, runtime_artifacts={"final.diff": "+++ new file"}
    )

    assert out["overall_score"] == 0.75
    assert passed is True  # 0.75 >= 0.5
    runner.judge.score_runtime.assert_called_once()


def test_evaluate_test_runtime_no_checklist_returns_one():
    """No llm_judge checklist in manifest → overall_score=1.0 without calling judge."""
    runner = _make_judge_runner({})
    tc = _make_test_case({})

    out, passed = runner.evaluate_test(tc, "runtime", "input", "output", None)

    assert out["overall_score"] == 1.0
    assert passed is True
    runner.judge.score_runtime.assert_not_called()


def test_evaluate_test_runtime_low_score_fails():
    """overall_score < 0.5 → passed=False."""
    checklist = [{"id": "x", "question": "Q?", "weight": 1.0}]
    judge_output = {"overall_score": 0.2, "criteria": [], "summary": "Poor", "raw": ""}
    runner = _make_judge_runner(judge_output)
    tc = _make_test_case({"llm_judge": {"checklist": checklist}})

    out, passed = runner.evaluate_test(tc, "runtime", "input", "output", None)

    assert out["overall_score"] == 0.2
    assert passed is False


def test_evaluate_test_unknown_category_no_random():
    """Unknown category must return overall_score=1.0 — never random."""
    runner = _make_judge_runner({})
    tc = _make_test_case({})

    results = set()
    for _ in range(10):
        out, passed = runner.evaluate_test(tc, "some_future_category", "input", "output", None)
        results.add(out["overall_score"])

    # Should always be 1.0, never random
    assert results == {1.0}
    assert passed is True
