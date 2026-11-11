"""Unit tests for runtime judge path in JudgeRunner.evaluate_test()."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.runners.judge import (
    JudgeRunner,
    _build_runtime_artifact_summary,
    _build_runtime_task_description,
)
from nichebench.providers.litellm_judge import JudgeError, LiteLLMJudge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_judge_runner(judge_output: dict[str, Any]) -> JudgeRunner:
    """Return a JudgeRunner whose underlying judge always returns judge_output."""
    runner = JudgeRunner.__new__(JudgeRunner)
    runner.model_str = "openai/gpt-4o"
    runner.model_config = {}
    mock_client = MagicMock()

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


def test_evaluate_test_unknown_category_fails_closed():
    """Unknown category must fail closed — never random, never auto-passed."""
    runner = _make_judge_runner({})
    tc = _make_test_case({})

    results = set()
    for _ in range(10):
        out, passed = runner.evaluate_test(tc, "some_future_category", "input", "output", None)
        results.add(out["overall_score"])

    # Deterministic and fail-closed.
    assert results == {0.0}
    assert passed is False
    assert out["pass"] is False
    assert out["error"] == "unknown category"
    assert out["error_kind"] == "config_error"


def _make_judge_error_runner(kind: str) -> JudgeRunner:
    """Return a JudgeRunner whose score_runtime always raises JudgeError."""
    runner = JudgeRunner.__new__(JudgeRunner)
    runner.model_str = "openai/gpt-4o"
    runner.model_config = {}
    runner.client = MagicMock()
    runner.judge = MagicMock(spec=LiteLLMJudge)
    runner.judge.score_runtime.side_effect = JudgeError(
        kind=kind,
        attempts=3,
        last_raw="[Error: HTTP 429]",
        last_reason="judge unavailable after 3 attempts",
    )
    return runner


def test_evaluate_runtime_judge_error_returns_error_envelope():
    """A judge failure is an error verdict, never a score and never an escape."""
    checklist = [{"id": "x", "question": "Q?", "weight": 1.0}]
    tc = _make_test_case({"llm_judge": {"checklist": checklist}})

    for kind in ("judge_unavailable", "judge_invalid_json"):
        runner = _make_judge_error_runner(kind)

        out, passed = runner.evaluate_test(tc, "runtime", "input", "output", None)

        assert out["error_kind"] == kind
        assert out["attempts"] == 3
        assert out["overall_score"] is None
        assert out["judge_score"] is None
        assert out["hybrid_score"] is None
        assert out["final_score"] is None
        assert isinstance(out["summary"], str) and out["summary"]
        assert passed is False


def test_evaluate_runtime_judge_error_does_not_raise():
    """JudgeError must be caught here, not reach the runtime executor."""
    tc = _make_test_case({"llm_judge": {"checklist": [{"id": "x", "question": "Q?", "weight": 1.0}]}})
    runner = _make_judge_error_runner("judge_unavailable")

    # No pytest.raises: the runner returns an envelope.
    out, passed = runner.evaluate_test(tc, "runtime", "input", "output", None)

    assert out["error_kind"] == "judge_unavailable"
    assert passed is False


class _FailingJudgeClient:
    """Client that always returns the same ``[Error: ...]`` payload."""

    def __init__(self, output: str):
        self.output = output
        self.calls = 0

    def generate(self, prompt: str, model: str, model_params=None):
        self.calls += 1
        return {"model": model, "output": self.output}


def test_evaluate_runtime_http_429_is_retried_then_reported_as_unavailable():
    """``[Error: HTTP 429]`` x3 → JudgeError(judge_unavailable) → error envelope.

    The judge adapter retries with its own backoff; the runner converts the
    exhausted JudgeError into a scoreless verdict.
    """
    checklist = [{"id": "x", "question": "Q?", "weight": 1.0}]
    tc = _make_test_case({"llm_judge": {"checklist": checklist}})

    client = _FailingJudgeClient("[Error: HTTP 429]")
    runner = JudgeRunner.__new__(JudgeRunner)
    runner.model_str = "openai/gpt-4o"
    runner.model_config = {}
    runner.client = client
    runner.judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    out, passed = runner.evaluate_test(tc, "runtime", "input", "output", None)

    assert client.calls == 3
    assert out["error_kind"] == "judge_unavailable"
    assert out["attempts"] == 3
    assert out["overall_score"] is None
    assert out["judge_score"] is None
    assert out["hybrid_score"] is None
    assert out["final_score"] is None
    assert passed is False
