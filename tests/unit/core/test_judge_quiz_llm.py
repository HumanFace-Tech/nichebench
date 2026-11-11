"""Quiz evaluation is judge-authoritative with a parser cross-check.

These tests cover the ``quiz`` branch of
:class:`nichebench.execution.runners.judge.JudgeRunner`:

* the verdict shape produced by the LLM judge (``pass`` / ``selected`` /
  ``score`` / ``overall_score`` / ``method == "llm"`` / ``attempts`` /
  ``judge_pass`` / ``judge_pass_mismatch`` / ``parser``),
* the judge's ``selected`` letter as the only authority for correctness —
  the deterministic parser is an audit cross-check (``parser.agrees``)
  and never decides the score,
* malformed quiz tasks failing with ``parse_reason == "invalid_task"``
  (no usable choices) or ``parse_reason == "invalid_gold"`` (unusable
  ``correct_choice``) and ``method == "validation"`` — without any judge
  call,
* ``JudgeError`` mapping to an ``error_kind`` envelope with null scores,
* unknown categories failing closed instead of being auto-passed,
* the hard guarantee that the quiz path never imports ``deepeval``,
* the dispatch-level ``judge_model`` / ``judge_method`` markers.

No network access and no model endpoint is required.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Dict, List, Optional

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.dispatch import execute_test
from nichebench.execution.runners.judge import JudgeRunner
from nichebench.execution.summary import update_summary
from nichebench.providers.litellm_judge import LiteLLMJudge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A judge verdict the tests can rely on: the judge picks B.
_B_VERDICT = json.dumps({"pass": True, "selected": "B", "score": 1, "explanation": "B is correct"})


class _BlockDeepevalFinder:
    """``sys.meta_path`` finder that refuses to import ``deepeval``."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
        if fullname == "deepeval" or fullname.startswith("deepeval."):
            raise ImportError(f"deepeval import blocked by test: {fullname}")


class _ExplodingJudge:
    """Stand-in for the LLM judge that fails loudly when it is used."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the quiz path must not call the judge for a data defect (attempted {name!r})")


class _ScriptedClient:
    """Judge client returning scripted outputs in order, then repeating the last."""

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, prompt: str, model: str, model_params: Any = None) -> Dict[str, Any]:
        self.calls += 1
        output = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        return {"model": model, "output": output}


def _make_runner(*outputs: str, judge: Any = None) -> JudgeRunner:
    """Build a judge runner; ``outputs`` are scripted judge responses."""
    runner = JudgeRunner.__new__(JudgeRunner)
    runner.model_str = "openai/gpt-4o"
    runner.model_config = {"parameters": {"temperature": 0}}
    runner.client = None
    runner.judge = judge if judge is not None else LiteLLMJudge(
        client=_ScriptedClient(*outputs) if outputs else _ScriptedClient(_B_VERDICT),
        sleeper=lambda _: None,
    )
    return runner


def _make_quiz_case(
    choices: Optional[List[str]] = None,
    correct_choice: Optional[str] = "B",
) -> TestCaseSpec:
    return TestCaseSpec(
        id="drupal_quiz_001",
        type="quiz",
        raw={},
        choices=["first", "second", "third", "fourth"] if choices is None else choices,
        correct_choice=correct_choice,
    )


def _evaluate(
    mut_output: str,
    test_case: Optional[TestCaseSpec] = None,
    *,
    outputs: tuple = (),
) -> Dict[str, Any]:
    """Run the quiz branch and return ``{"output", "passed"}``."""
    case = test_case if test_case is not None else _make_quiz_case()
    runner = _make_runner(*outputs)
    output, passed = runner.evaluate_test(case, "quiz", "question text", mut_output, None)
    return {"output": output, "passed": passed}


# ---------------------------------------------------------------------------
# Verdict shape: the judge is authoritative
# ---------------------------------------------------------------------------


def test_quiz_verdict_shape_for_correct_letter() -> None:
    result = _evaluate("B")

    output = result["output"]
    assert result["passed"] is True
    assert output["pass"] is True
    assert output["selected"] == "B"
    assert output["score"] == 1.0
    assert output["overall_score"] == 1.0
    assert output["method"] == "llm"
    assert output["attempts"] == 1
    assert output["judge_pass"] is True
    assert output["judge_pass_mismatch"] is False
    assert output["explanation"]
    assert output["raw"] == _B_VERDICT
    assert output["parse_reason"] == "ok"
    assert output["parser"] == {"selected": "B", "parse_reason": "ok", "agrees": True}


def test_noisy_answer_is_scored_by_the_judge_not_the_parser() -> None:
    """``"A is deprecated, so B"`` parses as A but the judge picks B.

    The judge's letter decides: the task passes with the judge's B, and the
    parser cross-check records the disagreement.
    """
    result = _evaluate("A is deprecated, so B")

    output = result["output"]
    assert result["passed"] is True
    assert output["pass"] is True
    assert output["selected"] == "B"
    assert output["score"] == 1.0
    assert output["parser"]["selected"] == "A"
    assert output["parser"]["agrees"] is False


def test_score_follows_the_judge_even_when_the_parser_matches_gold() -> None:
    """Judge says B while the parser (and gold) say A: the score is 0.0."""
    case = _make_quiz_case(correct_choice="A")
    result = _evaluate("A", case, outputs=(_B_VERDICT,))

    output = result["output"]
    # The parser agrees with gold, but the judge is the authority.
    assert result["passed"] is False
    assert output["selected"] == "B"
    assert output["score"] == 0.0
    assert output["parser"]["selected"] == "A"
    assert output["parser"]["agrees"] is False


def test_judge_boolean_disagreement_is_recorded() -> None:
    """The judge's own ``pass`` is audit-only — ``selected`` decides."""
    verdict = json.dumps({"pass": False, "selected": "B", "score": 0, "explanation": "claims wrong"})
    result = _evaluate("B", outputs=(verdict,))

    output = result["output"]
    assert result["passed"] is True
    assert output["pass"] is True
    assert output["score"] == 1.0
    assert output["judge_pass"] is False
    assert output["judge_pass_mismatch"] is True


def test_parser_agrees_is_none_when_the_parser_finds_no_letter() -> None:
    """An empty answer parses to nothing → ``agrees`` is ``None`` (unknown)."""
    result = _evaluate("")

    output = result["output"]
    assert output["selected"] == "B"  # the scripted judge still picks B
    assert output["parser"]["selected"] is None
    assert output["parser"]["agrees"] is None
    assert output["parser"]["parse_reason"] == "empty"
    assert output["parse_reason"] == "empty"


def test_judge_selection_out_of_range_retries_then_succeeds() -> None:
    """First judge attempt returns Z (unoffered) → retried; second returns B."""
    out_of_range = json.dumps({"pass": True, "selected": "Z", "score": 1, "explanation": "Z"})
    client = _ScriptedClient(out_of_range, _B_VERDICT)
    runner = _make_runner(judge=LiteLLMJudge(client=client, sleeper=lambda _: None))

    output, passed = runner.evaluate_test(_make_quiz_case(), "quiz", "question text", "B", None)

    assert passed is True
    assert output["pass"] is True
    assert output["selected"] == "B"
    assert output["attempts"] == 2
    assert client.calls == 2


# ---------------------------------------------------------------------------
# JudgeError → error envelope
# ---------------------------------------------------------------------------


def _assert_judge_error_envelope(output: Dict[str, Any], passed: bool, kind: str) -> None:
    assert passed is False
    assert output["error_kind"] == kind
    assert isinstance(output["error"], str) and output["error"]
    assert output["attempts"] == 3
    assert output["score"] is None
    assert output["overall_score"] is None


def test_judge_api_error_maps_to_judge_unavailable_envelope() -> None:
    runner = _make_runner("[Error: provider down]")
    output, passed = runner.evaluate_test(_make_quiz_case(), "quiz", "question text", "B", None)
    _assert_judge_error_envelope(output, passed, "judge_unavailable")


def test_judge_invalid_json_maps_to_error_envelope() -> None:
    runner = _make_runner("not json at all")
    output, passed = runner.evaluate_test(_make_quiz_case(), "quiz", "question text", "B", None)
    _assert_judge_error_envelope(output, passed, "judge_invalid_json")


def test_judge_missing_selected_maps_to_error_envelope() -> None:
    runner = _make_runner('{"pass": true, "score": 1, "explanation": "no letter"}')
    output, passed = runner.evaluate_test(_make_quiz_case(), "quiz", "question text", "B", None)
    _assert_judge_error_envelope(output, passed, "judge_missing_selected")


def test_judge_out_of_range_selected_maps_to_error_envelope() -> None:
    runner = _make_runner('{"pass": true, "selected": "Z", "score": 1}')
    output, passed = runner.evaluate_test(_make_quiz_case(), "quiz", "question text", "B", None)
    _assert_judge_error_envelope(output, passed, "judge_out_of_range")


def test_dispatch_judge_error_sets_error_kind_and_fails(tmp_path) -> None:
    """A quiz judge failure flows through dispatch as a ``judge_*`` error."""
    result = execute_test(
        test_case=_make_quiz_case(),
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("B"),
        judge_runner=_make_runner("[Error: provider down]"),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.passed is False
    assert result.error_kind == "judge_unavailable"
    assert result.judge_attempts == 3

    # The summary excludes the row from scoring and counts it as a judge error.
    summary_path = tmp_path / "summary.json"
    update_summary(
        results=[result],
        summary_path=summary_path,
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner_model_config={},
        judge_runner_model_config={"provider": "openai", "model": "gpt-4o", "parameters": {}},
        profile=None,
        eval_config={},
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["judge_errors"] == 1
    assert summary["attempted_total"] == 1
    assert summary["total"] == 0
    assert summary["passed"] == 0
    assert summary["failed"] == 0
    assert summary["avg_score"] is None


def test_dispatch_judge_error_serializes_null_scores(tmp_path) -> None:
    """A judge failure must never serialize a fabricated zero.

    ``to_dict()`` keeps the error metadata (``error_kind``, ``judge_attempts``)
    and reports null scores, so the row can never be mistaken for a scored
    model failure.
    """
    result = execute_test(
        test_case=_make_quiz_case(),
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("B"),
        judge_runner=_make_runner("[Error: provider down]"),
        system_prompt=None,
        judge_system_prompt=None,
    )

    payload = result.to_dict()

    assert payload["judge_score"] is None
    assert payload["final_score"] is None
    assert payload["deterministic_score"] is None
    assert payload["pass"] is False
    assert payload["error_kind"] == "judge_unavailable"
    assert payload["judge_attempts"] == 3

    # The row is counted as a judge error and excluded from scoring.
    summary_path = tmp_path / "summary.json"
    update_summary(
        results=[result],
        summary_path=summary_path,
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner_model_config={},
        judge_runner_model_config={},
        profile=None,
        eval_config={},
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["judge_errors"] == 1
    assert summary["config_errors"] == 0
    assert summary["total"] == 0
    assert summary["avg_score"] is None


# ---------------------------------------------------------------------------
# Explicit abstention — a scored zero, not a judge error
# ---------------------------------------------------------------------------

_ABSTENTION_VERDICT = json.dumps(
    {"pass": False, "selected": None, "score": 0, "explanation": "The answer is ambiguous"}
)


def test_explicit_abstention_is_scored_zero() -> None:
    """``"selected": null`` with a verdict and an explanation is a model failure."""
    client = _ScriptedClient(_ABSTENTION_VERDICT)
    runner = _make_runner(judge=LiteLLMJudge(client=client, sleeper=lambda _: None))

    output, passed = runner.evaluate_test(_make_quiz_case(), "quiz", "question text", "B", None)

    assert passed is False
    assert output["status"] == "abstained"
    assert output["selected"] is None
    assert output["pass"] is False
    assert output["score"] == 0.0
    assert output["overall_score"] == 0.0
    assert output["explanation"] == "The answer is ambiguous"
    assert output["judge_pass"] is False
    assert output["attempts"] == 1
    # No error kind: an abstention is a scored model failure.
    assert "error_kind" not in output
    # Never retried.
    assert client.calls == 1


def test_abstention_is_included_in_the_quiz_totals(tmp_path) -> None:
    """An abstention counts in the denominator — it is not excluded."""
    result = execute_test(
        test_case=_make_quiz_case(),
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("B"),
        judge_runner=_make_runner(_ABSTENTION_VERDICT),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.passed is False
    assert result.error_kind is None

    payload = result.to_dict()
    assert payload["judge_score"] == 0.0
    assert payload["final_score"] == 0.0
    assert payload["deterministic_score"] is None

    summary_path = tmp_path / "summary.json"
    update_summary(
        results=[result],
        summary_path=summary_path,
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner_model_config={},
        judge_runner_model_config={},
        profile=None,
        eval_config={},
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["judge_errors"] == 0
    assert summary["config_errors"] == 0
    assert summary["total"] == 1
    assert summary["failed"] == 1
    assert summary["avg_score"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Malformed quiz tasks fail closed — without any judge call
# ---------------------------------------------------------------------------


def _assert_invalid_task(result: Dict[str, Any]) -> None:
    assert result["passed"] is False
    output = result["output"]
    assert output == {
        "pass": False,
        "selected": None,
        "score": 0.0,
        "explanation": "Quiz task is missing choices",
        "method": "validation",
        "parse_reason": "invalid_task",
        "error_kind": "config_error",
    }


def _assert_invalid_gold(result: Dict[str, Any]) -> None:
    assert result["passed"] is False
    output = result["output"]
    assert output == {
        "pass": False,
        "selected": None,
        "score": 0.0,
        "explanation": "Quiz task is missing or invalid correct_choice",
        "method": "validation",
        "parse_reason": "invalid_gold",
        "error_kind": "config_error",
    }


def _evaluate_with_exploding_judge(
    mut_output: str, test_case: TestCaseSpec
) -> Dict[str, Any]:
    """Run a data-defect quiz against a judge that must never be touched."""
    runner = _make_runner(judge=_ExplodingJudge())
    output, passed = runner.evaluate_test(test_case, "quiz", "question text", mut_output, None)
    return {"output": output, "passed": passed}


def test_quiz_without_choices_is_invalid_task() -> None:
    _assert_invalid_task(_evaluate_with_exploding_judge("B", _make_quiz_case(choices=[])))


def test_quiz_with_empty_choices_is_invalid_task() -> None:
    _assert_invalid_task(_evaluate_with_exploding_judge("B", _make_quiz_case(choices=[""])))


def test_quiz_without_correct_choice_is_invalid_gold() -> None:
    _assert_invalid_gold(_evaluate_with_exploding_judge("B", _make_quiz_case(correct_choice=None)))


def test_quiz_with_blank_correct_choice_is_invalid_gold() -> None:
    _assert_invalid_gold(_evaluate_with_exploding_judge("B", _make_quiz_case(correct_choice="   ")))


def test_quiz_with_non_string_correct_choice_is_invalid_gold() -> None:
    _assert_invalid_gold(
        _evaluate_with_exploding_judge("B", _make_quiz_case(correct_choice=2))  # type: ignore[arg-type]
    )


def test_quiz_with_single_choice_is_invalid_task() -> None:
    _assert_invalid_task(_evaluate_with_exploding_judge("A", _make_quiz_case(choices=["only"])))


def test_quiz_with_too_many_choices_is_invalid_task() -> None:
    choices = [f"choice {index}" for index in range(27)]
    result = _evaluate_with_exploding_judge("A", _make_quiz_case(choices=choices))
    assert result["passed"] is False
    assert result["output"]["method"] == "validation"
    assert result["output"]["parse_reason"] == "invalid_task"
    assert "unsupported number of choices" in result["output"]["explanation"]


def test_quiz_with_out_of_range_gold_is_invalid_gold() -> None:
    _assert_invalid_gold(_evaluate_with_exploding_judge("B", _make_quiz_case(correct_choice="Z")))


def test_quiz_with_multi_letter_gold_is_invalid_gold() -> None:
    _assert_invalid_gold(_evaluate_with_exploding_judge("B", _make_quiz_case(correct_choice="AB")))


def test_quiz_with_missing_choices_and_bad_gold_reports_invalid_task() -> None:
    """Choices are checked first — the choice defect is the more fundamental one."""
    result = _evaluate_with_exploding_judge("B", _make_quiz_case(choices=[], correct_choice="Z"))
    assert result["output"]["parse_reason"] == "invalid_task"
    assert result["output"]["explanation"] == "Quiz task is missing choices"


def test_quiz_with_lowercase_gold_is_valid() -> None:
    """A lower-case gold letter is normalised, not rejected."""
    result = _evaluate("B", _make_quiz_case(correct_choice="b"))
    assert result["passed"] is True
    assert result["output"]["method"] == "llm"
    assert result["output"]["parse_reason"] == "ok"


# ---------------------------------------------------------------------------
# Invalid quiz task data is a config error — never a model failure
# ---------------------------------------------------------------------------


def _dispatch_invalid_quiz(test_case: TestCaseSpec, tmp_path) -> Dict[str, Any]:
    """Dispatch an invalid quiz task and return the written summary."""
    result = execute_test(
        test_case=test_case,
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("B"),
        judge_runner=_make_runner(judge=_ExplodingJudge()),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.passed is False
    assert result.error_kind == "config_error"

    payload = result.to_dict()
    # No fabricated zero: a config error is not a model result.
    assert payload["judge_score"] is None
    assert payload["final_score"] is None
    assert payload["deterministic_score"] is None
    assert payload["error_kind"] == "config_error"
    assert payload["pass"] is False
    # The validation verdict keeps its method and parse reason.
    assert payload["judge_output"]["method"] == "validation"
    assert payload["judge_output"]["parse_reason"] in ("invalid_task", "invalid_gold")

    summary_path = tmp_path / "summary.json"
    update_summary(
        results=[result],
        summary_path=summary_path,
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner_model_config={},
        judge_runner_model_config={},
        profile=None,
        eval_config={},
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["config_errors"] == 1
    assert summary["judge_errors"] == 0
    assert summary["attempted_total"] == 1
    assert summary["total"] == 0
    assert summary["passed"] == 0
    assert summary["failed"] == 0
    assert summary["avg_score"] is None
    return summary


def test_invalid_gold_quiz_is_counted_as_a_config_error(tmp_path) -> None:
    """A missing gold is a data defect: no judge call, no score."""
    _dispatch_invalid_quiz(_make_quiz_case(correct_choice=None), tmp_path)


def test_missing_choices_quiz_is_counted_as_a_config_error(tmp_path) -> None:
    """Missing choices are a data defect: no judge call, no score."""
    _dispatch_invalid_quiz(_make_quiz_case(choices=[]), tmp_path)


def test_invalid_quiz_task_does_not_drag_the_pass_rate(tmp_path) -> None:
    """A config error never lowers the average of the scored quiz rows."""
    good = execute_test(
        test_case=_make_quiz_case(),
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("B"),
        judge_runner=_make_runner(),
        system_prompt=None,
        judge_system_prompt=None,
    )
    broken = execute_test(
        test_case=_make_quiz_case(correct_choice=None),
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner("B"),
        judge_runner=_make_runner(judge=_ExplodingJudge()),
        system_prompt=None,
        judge_system_prompt=None,
    )

    summary_path = tmp_path / "summary.json"
    update_summary(
        results=[good, broken],
        summary_path=summary_path,
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner_model_config={},
        judge_runner_model_config={},
        profile=None,
        eval_config={},
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["config_errors"] == 1
    assert summary["total"] == 1
    assert summary["passed"] == 1
    assert summary["avg_score"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Unknown categories fail closed
# ---------------------------------------------------------------------------


def test_unknown_category_fails_closed() -> None:
    """An unknown category must never be auto-passed."""
    output, passed = _make_runner().evaluate_test(
        _make_quiz_case(),
        "not_a_real_category",
        "question text",
        "B",
        None,
    )

    assert passed is False
    assert output["pass"] is False
    assert output["score"] == 0.0
    assert output["overall_score"] == 0.0
    assert output["error"] == "unknown category"
    assert output["error_kind"] == "config_error"
    assert output["explanation"] == "Unknown category - failing closed"


# ---------------------------------------------------------------------------
# DeepEval-free guarantees
# ---------------------------------------------------------------------------


def test_quiz_path_imports_no_deepeval_module() -> None:
    """Evaluating a quiz must not pull any ``deepeval`` module in."""
    before = {name for name in sys.modules if name == "deepeval" or name.startswith("deepeval.")}

    finder = _BlockDeepevalFinder()
    sys.meta_path.insert(0, finder)
    try:
        result = _evaluate("B")
    finally:
        sys.meta_path.remove(finder)

    assert result["passed"] is True
    after = {name for name in sys.modules if name == "deepeval" or name.startswith("deepeval.")}
    assert after == before


def test_deleted_quiz_metric_modules_are_gone() -> None:
    """The old DeepEval quiz metric and the dead QuizMetric are removed."""
    import importlib.util

    for module_name in (
        "nichebench.metrics.deepeval_quiz_metric",
        "nichebench.metrics.quiz_metric",
    ):
        assert importlib.util.find_spec(module_name) is None


def test_quiz_path_works_with_deepeval_blocked_at_import_time() -> None:
    """Importing the judge runner and scoring a quiz must not need DeepEval."""
    script = """
import json
import sys


class _BlockDeepeval:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "deepeval" or fullname.startswith("deepeval."):
            raise ImportError("deepeval import blocked by test: " + fullname)
        return None


sys.meta_path.insert(0, _BlockDeepeval())

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.runners.judge import JudgeRunner


class _FakeJudge:
    def score_quiz(self, **kwargs):
        return {
            "pass": True,
            "selected": "B",
            "score": 1.0,
            "overall_score": 1.0,
            "explanation": "B is correct",
            "judge_pass": True,
            "judge_pass_mismatch": False,
            "attempts": 1,
            "raw": '{"pass": true, "selected": "B"}',
        }


runner = JudgeRunner.__new__(JudgeRunner)
runner.model_str = "openai/gpt-4o"
runner.model_config = {"parameters": {}}
runner.judge = _FakeJudge()

case = TestCaseSpec(
    id="drupal_quiz_001",
    type="quiz",
    raw={},
    choices=["first", "second", "third", "fourth"],
    correct_choice="B",
)

output, passed = runner.evaluate_test(case, "quiz", "question text", "The answer is B", None)

print(
    json.dumps(
        {
            "output": output,
            "passed": passed,
            "deepeval_loaded": any(
                name == "deepeval" or name.startswith("deepeval.") for name in sys.modules
            ),
        }
    )
)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    assert payload["deepeval_loaded"] is False
    assert payload["passed"] is True
    assert payload["output"]["method"] == "llm"
    assert payload["output"]["selected"] == "B"
    assert payload["output"]["parse_reason"] == "ok"
    assert payload["output"]["parser"]["agrees"] is True


# ---------------------------------------------------------------------------
# Dispatch wiring
# ---------------------------------------------------------------------------


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
        return self.output, "question text"


def test_dispatch_quiz_result_carries_the_configured_judge() -> None:
    result = execute_test(
        test_case=_make_quiz_case(),
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-5",
        mut_runner=_FakeMutRunner("B"),
        judge_runner=_make_runner(),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.passed is True
    assert result.judge_model == "openai/gpt-5"
    assert result.judge_method == "llm"

    serialized = result.to_dict()
    assert serialized["judge_model"] == "openai/gpt-5"
    assert serialized["judge_method"] == "llm"
    assert serialized["judge_output"]["method"] == "llm"


def test_dispatch_quiz_failure_is_marked_llm() -> None:
    wrong_verdict = json.dumps({"pass": False, "selected": "A", "score": 0, "explanation": "A"})
    result = execute_test(
        test_case=_make_quiz_case(),
        category="quiz",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-5",
        mut_runner=_FakeMutRunner("A"),
        judge_runner=_make_runner(wrong_verdict),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.passed is False
    assert result.judge_method == "llm"
    assert result.to_dict()["judge_method"] == "llm"
    assert result.judge_output["selected"] == "A"
    assert result.judge_output["score"] == 0.0
