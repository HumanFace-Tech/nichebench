"""Code/bug scoring calls the LLM judge directly — no DeepEval wrapper.

Covers the code-generation v2 C1/C2/C3 slices:

* ``JudgeRunner.evaluate_test`` routes ``code_generation`` to
  ``LiteLLMJudge.score_code_generation`` and
  ``bug_fixing`` to ``LiteLLMJudge.score_bug_fixing`` with the expected
  arguments, returning the judge output unchanged.  The removed
  ``code_agent`` category fails closed and never reaches the judge.
* Pass/fail uses the effective ``evaluation.judge_threshold`` of the run,
  falling back to ``settings.judge_threshold`` (default 0.7), on
  ``overall_score`` — the config flow is covered in
  ``test_judge_threshold_config.py``.
* A ``JudgeError`` from the judge becomes an ``error_kind`` verdict with
  ``passed=False`` — never a fabricated score.
* The judge's own ``overall_score`` is audit-only: an inflated ``1.2``
  with all-false criteria ends at ``0.0`` / failed.
* The code/bug path works with ``deepeval`` blocked at import time.
* ``TestResult.to_dict()`` maps the recomputed ``overall_score`` to
  ``judge_score`` / ``final_score`` (clamped) and carries
  ``judge_attempts``.
* ``dispatch.execute_test`` propagates ``error_kind`` / ``attempts``
  from the judge output onto the result.
* The judge-path cases use a structurally valid MUT response (declared
  path + fenced block) — the C4 sanity gate hard-fails no-code responses
  before the judge (see ``test_judge_sanity.py``).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.dispatch import execute_test
from nichebench.execution.result import TestResult
from nichebench.execution.runners.judge import JudgeRunner
from nichebench.providers.litellm_judge import JudgeError, LiteLLMJudge

_CODE_OUTPUT: Dict[str, Any] = {
    "criteria": [{"criterion": "c", "pass": True, "explanation": "ok"}],
    "overall_score": 0.9,
    "summary": "ok",
    "raw": "",
}

_BUG_OUTPUT: Dict[str, Any] = {
    "criteria": [{"criterion": "c", "pass": False, "explanation": "nope"}],
    "overall_score": 0.4,
    "summary": "ok",
    "raw": "",
}

# Structurally valid MUT response (declared path + fenced block) so the C4
# sanity gate lets these tests reach the judge.  It yields zero findings.
_VALID_MUT = (
    "FILENAME: src/solution.py\n"
    "\n"
    "```python\n"
    "print('ok')\n"
    "```\n"
)


class _FakeJudge:
    """Stand-in for :class:`LiteLLMJudge` recording every call."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def score_code_generation(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("code_generation", kwargs))
        return dict(_CODE_OUTPUT)

    def score_bug_fixing(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("bug_fixing", kwargs))
        return dict(_BUG_OUTPUT)


class _BlockDeepevalFinder:
    """``sys.meta_path`` finder that refuses to import ``deepeval``."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
        if fullname == "deepeval" or fullname.startswith("deepeval."):
            raise ImportError(f"deepeval import blocked by test: {fullname}")


def _make_runner(judge: _FakeJudge) -> JudgeRunner:
    runner = JudgeRunner.__new__(JudgeRunner)
    runner.model_str = "openai/gpt-4o"
    runner.model_config = {"parameters": {"temperature": 0.0}}
    runner.client = None
    runner.judge = judge  # type: ignore[assignment]
    return runner


def _make_case(category: str) -> TestCaseSpec:
    return TestCaseSpec(
        id=f"{category}_001",
        type=category,
        raw={"prompt": "task", "judge_notes": "be strict"},
        checklist=["criterion one", "criterion two"],
    )


def test_code_generation_calls_score_code_generation_and_passes() -> None:
    judge = _FakeJudge()
    output, passed = _make_runner(judge).evaluate_test(
        _make_case("code_generation"), "code_generation", "the prompt", _VALID_MUT, "be a judge"
    )

    assert output == {**_CODE_OUTPUT, "sanity_findings": []}
    assert passed is True

    assert len(judge.calls) == 1
    method, kwargs = judge.calls[0]
    assert method == "code_generation"
    assert kwargs["prompt"] == "the prompt"
    assert kwargs["generated_code"] == _VALID_MUT
    assert kwargs["checklist"] == ["criterion one", "criterion two"]
    assert kwargs["model"] == "openai/gpt-4o"
    assert kwargs["model_params"] == {"temperature": 0.0}
    assert kwargs["system_prompt"] == "be a judge"
    assert kwargs["judge_notes"] == "be strict"


def test_bug_fixing_calls_score_bug_fixing_and_fails_below_threshold() -> None:
    judge = _FakeJudge()
    output, passed = _make_runner(judge).evaluate_test(
        _make_case("bug_fixing"), "bug_fixing", "the bug", _VALID_MUT, None
    )

    assert output == {**_BUG_OUTPUT, "sanity_findings": []}
    assert passed is False

    assert len(judge.calls) == 1
    method, kwargs = judge.calls[0]
    assert method == "bug_fixing"
    assert kwargs["bug_description"] == "the bug"
    assert kwargs["proposed_fix"] == _VALID_MUT
    assert kwargs["checklist"] == ["criterion one", "criterion two"]
    assert kwargs["system_prompt"] is None
    assert kwargs["judge_notes"] == "be strict"


def test_code_agent_fails_closed_and_never_reaches_the_judge() -> None:
    """The removed ``code_agent`` category is not scored as ``code_generation``."""
    judge = _FakeJudge()
    output, passed = _make_runner(judge).evaluate_test(
        _make_case("code_agent"), "code_agent", "prompt", _VALID_MUT, None
    )

    assert passed is False
    assert output["error_kind"] == "config_error"
    assert judge.calls == []


def test_empty_checklist_is_passed_as_empty_list() -> None:
    judge = _FakeJudge()
    case = TestCaseSpec(id="code_002", type="code_generation", raw={}, checklist=None)
    _make_runner(judge).evaluate_test(case, "code_generation", "p", _VALID_MUT, None)

    assert judge.calls[0][1]["checklist"] == []
    assert judge.calls[0][1]["judge_notes"] is None


def test_code_path_needs_no_deepeval_import() -> None:
    """Scoring code/bug output must not import (or need) ``deepeval``."""
    before = {name for name in sys.modules if name == "deepeval" or name.startswith("deepeval.")}

    judge = _FakeJudge()
    runner = _make_runner(judge)
    finder = _BlockDeepevalFinder()
    sys.meta_path.insert(0, finder)
    try:
        code_passed = runner.evaluate_test(_make_case("code_generation"), "code_generation", "p", _VALID_MUT, None)[1]
        bug_passed = runner.evaluate_test(_make_case("bug_fixing"), "bug_fixing", "p", _VALID_MUT, None)[1]
    finally:
        sys.meta_path.remove(finder)

    after = {name for name in sys.modules if name == "deepeval" or name.startswith("deepeval.")}
    assert after == before
    assert code_passed is True
    assert bug_passed is False


def _make_code_result(judge_output: Dict[str, Any], category: str = "code_generation") -> TestResult:
    case = TestCaseSpec(id=f"{category}_001", type=category, raw={"prompt": "task"})
    result = TestResult(
        framework="drupal",
        category=category,
        test_case=case,
        mut_model="openai/gpt-4o",
        judge_model="openai/gpt-4o",
    )
    result.judge_output = judge_output
    result.passed = judge_output.get("overall_score", 0.0) >= 0.7
    return result


def test_to_dict_maps_overall_score_to_judge_and_final_score() -> None:
    payload = _make_code_result(dict(_CODE_OUTPUT)).to_dict()

    assert payload["judge_score"] == 0.9
    assert payload["final_score"] == 0.9
    assert payload["deterministic_score"] is None
    assert payload["judge_method"] == "llm"
    assert payload["judge_attempts"] == 1
    assert "error_kind" not in payload
    assert "sanity_findings" not in payload


def test_to_dict_clamps_out_of_range_overall_score() -> None:
    payload = _make_code_result({"overall_score": 1.2, "criteria": []}).to_dict()

    assert payload["judge_score"] == 1.0
    assert payload["final_score"] == 1.0


def test_to_dict_includes_error_kind_and_attempts_when_set() -> None:
    result = _make_code_result({"criteria": []})
    result.error_kind = "judge_invalid_json"
    result.judge_attempts = 3

    payload = result.to_dict()
    assert payload["error_kind"] == "judge_invalid_json"
    assert payload["judge_attempts"] == 3


class _FakeMutRunner:
    def __init__(self, output: str) -> None:
        self.output = output

    def run_test(
        self,
        test_case: TestCaseSpec,
        system_prompt: Optional[str],
        category: str,
        runner: Any = None,
    ) -> Any:
        return self.output, "user input"


class _StubJudgeRunner:
    def __init__(self, judge_output: Dict[str, Any], passed: bool) -> None:
        self.judge_output = judge_output
        self.passed = passed

    def evaluate_test(
        self,
        test_case: TestCaseSpec,
        category: str,
        user_input: str,
        mut_output: str,
        judge_system_prompt: Optional[str],
        runtime_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        return self.judge_output, self.passed


def test_dispatch_propagates_error_kind_and_attempts() -> None:
    result = execute_test(
        test_case=_make_case("code_generation"),
        category="code_generation",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("code"),
        judge_runner=_StubJudgeRunner(
            {"error_kind": "judge_invalid_json", "attempts": 3, "overall_score": 0.0},
            False,
        ),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.error_kind == "judge_invalid_json"
    assert result.judge_attempts == 3
    payload = result.to_dict()
    assert payload["error_kind"] == "judge_invalid_json"
    assert payload["judge_attempts"] == 3


def test_dispatch_leaves_error_fields_unset_for_a_clean_verdict() -> None:
    result = execute_test(
        test_case=_make_case("code_generation"),
        category="code_generation",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("code"),
        judge_runner=_StubJudgeRunner(dict(_CODE_OUTPUT), True),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.error_kind is None
    assert result.judge_attempts == 1
    payload = result.to_dict()
    assert "error_kind" not in payload
    assert payload["judge_attempts"] == 1


# ─── C2/C3: judge errors, recomputation, threshold ────────────────────────────

_ERROR_OUTPUT = {
    "error_kind": "judge_unavailable",
    "error": "[Error: provider down]",
    "attempts": 3,
    "judge_score": None,
    "final_score": None,
}


class _ErrorJudge:
    """Stand-in judge that always raises the way ``LiteLLMJudge`` does."""

    def score_code_generation(self, **kwargs: Any) -> Dict[str, Any]:
        raise JudgeError(
            kind="judge_unavailable",
            attempts=3,
            last_raw="[Error: provider down]",
            last_reason="[Error: provider down]",
        )

    def score_bug_fixing(self, **kwargs: Any) -> Dict[str, Any]:
        raise JudgeError(
            kind="judge_unavailable",
            attempts=3,
            last_raw="[Error: provider down]",
            last_reason="[Error: provider down]",
        )


class _StaticClient:
    """Client replaying one fixed judge response."""

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    def generate(self, prompt: str, model: str, model_params=None) -> Dict[str, Any]:
        self.calls += 1
        return {"model": model, "output": self.output}


def test_judge_error_returns_error_kind_and_fails() -> None:
    runner = _make_runner(_ErrorJudge())
    output, passed = runner.evaluate_test(
        _make_case("code_generation"), "code_generation", "the prompt", _VALID_MUT, None
    )

    assert passed is False
    assert output == _ERROR_OUTPUT


def test_judge_error_surfaces_through_dispatch() -> None:
    result = execute_test(
        test_case=_make_case("code_generation"),
        category="code_generation",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner(_VALID_MUT),
        judge_runner=_make_runner(_ErrorJudge()),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.passed is False
    assert result.error_kind == "judge_unavailable"
    assert result.judge_attempts == 3

    payload = result.to_dict()
    assert payload["pass"] is False
    assert payload["error_kind"] == "judge_unavailable"
    assert payload["judge_attempts"] == 3
    assert payload["judge_score"] is None
    assert payload["final_score"] is None
    # The MUT output stays untouched for forensics.
    assert result.mut_output == _VALID_MUT


def test_inflated_overall_score_is_recomputed_from_criteria() -> None:
    """Judge reports 1.2 with all criteria failed → 0.0 / failed, no retry."""
    verdict = json.dumps(
        {
            "criteria": [
                {"criterion": "criterion one", "pass": False, "explanation": "nope"},
                {"criterion": "criterion two", "pass": False, "explanation": "nope"},
            ],
            "overall_score": 1.2,
            "summary": "ok",
        }
    )
    client = _StaticClient(verdict)
    runner = _make_runner(LiteLLMJudge(client=client))

    output, passed = runner.evaluate_test(
        _make_case("code_generation"), "code_generation", "the prompt", _VALID_MUT, None
    )

    assert client.calls == 1
    assert output["overall_score"] == 0.0
    assert output["judge_overall_score"] == 1.2
    assert output["attempts"] == 1
    assert passed is False


def test_attempts_from_a_clean_verdict_reach_the_result() -> None:
    result = execute_test(
        test_case=_make_case("code_generation"),
        category="code_generation",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("code"),
        judge_runner=_StubJudgeRunner({**_CODE_OUTPUT, "attempts": 2}, True),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.judge_attempts == 2
    assert result.to_dict()["judge_attempts"] == 2


def test_dispatch_populates_judge_and_final_score_for_code() -> None:
    result = execute_test(
        test_case=_make_case("code_generation"),
        category="code_generation",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("code"),
        judge_runner=_StubJudgeRunner(dict(_CODE_OUTPUT), True),
        system_prompt=None,
        judge_system_prompt=None,
    )

    payload = result.to_dict()
    assert payload["judge_score"] == 0.9
    assert payload["final_score"] == 0.9
    assert payload["deterministic_score"] is None
    assert payload["judge_method"] == "llm"
