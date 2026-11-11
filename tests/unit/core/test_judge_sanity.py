"""C4: the structural sanity gate runs before the judge.

Covers ``JudgeRunner._evaluate_with_judge`` and the dispatch plumbing:

* an empty MUT response never reaches the judge and returns a hard-fail
  verdict (``error_kind`` = check name, all scores ``0.0``);
* a non-empty but code-free response (prose) is advisory — it still
  reaches the judge;
* a valid response still calls the judge and carries the (advisory)
  ``sanity_findings`` on the judge output;
* ``evaluation.code_sanity_checks: false`` skips the layer entirely;
* ``dispatch.execute_test`` copies the findings onto ``TestResult`` and
  ``TestResult.to_dict()`` exposes them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.dispatch import execute_test
from nichebench.execution.runners.judge import JudgeRunner

_JUDGE_OUTPUT: Dict[str, Any] = {
    "criteria": [{"criterion": "c", "pass": True, "explanation": "ok"}],
    "overall_score": 0.9,
    "summary": "ok",
    "raw": "",
}

# Nothing at all to judge — the only ``sanity_no_code`` hard failure
# besides truncation.
_EMPTY_RESPONSE = ""

# Non-empty but with no fence, no marker and no code shape: advisory, so
# it still reaches the judge.
_NO_CODE_RESPONSE = "I'm afraid I cannot help with that request."

# Structurally clean: declared path + fenced block → zero findings, so the
# judge-path assertions stay deterministic on any host.
_VALID_RESPONSE = (
    "FILENAME: src/solution.py\n"
    "\n"
    "```python\n"
    "print('ok')\n"
    "```\n"
)

# Code without a declared path → exactly one advisory finding.
_ADVISORY_RESPONSE = "Here is the code:\n\n```python\nprint('ok')\n```"

# Valid code that arrives without a fence and without a marker.
_UNFENCED_CODE_RESPONSE = "<?php\nfunction answer(): int { return 42; }\n"


class _FakeJudge:
    """Stand-in for :class:`LiteLLMJudge` recording every call."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def score_code_generation(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("code_generation", kwargs))
        return dict(_JUDGE_OUTPUT)

    def score_bug_fixing(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("bug_fixing", kwargs))
        return dict(_JUDGE_OUTPUT)


class _StubConfig:
    """Config stand-in exposing only the evaluation switch."""

    def __init__(self, code_sanity_checks: bool) -> None:
        self.code_sanity_checks = code_sanity_checks

    def get_evaluation_config(self) -> Dict[str, Any]:
        return {"code_sanity_checks": self.code_sanity_checks}


@pytest.fixture(autouse=True)
def _pin_sanity_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``code_sanity_checks`` on so a local ``nichebench.yml`` cannot skew tests."""
    monkeypatch.setattr(
        "nichebench.execution.runners.judge.get_config",
        lambda: _StubConfig(True),
    )


def _make_runner(judge: _FakeJudge) -> JudgeRunner:
    runner = JudgeRunner.__new__(JudgeRunner)
    runner.model_str = "openai/gpt-4o"
    runner.model_config = {"parameters": {"temperature": 0.0}}
    runner.client = None
    runner.judge = judge  # type: ignore[assignment]
    return runner


def _make_case() -> TestCaseSpec:
    return TestCaseSpec(
        id="code_generation_001",
        type="code_generation",
        raw={"prompt": "task"},
        checklist=["criterion one"],
    )


class _FakeMutRunner:
    def __init__(self, output: str) -> None:
        self.output = output

    def run_test(
        self,
        test_case: TestCaseSpec,
        system_prompt: Optional[str],
        category: str,
        runner: Any = None,
    ) -> Tuple[str, str]:
        return self.output, "user input"


def _execute(mut_output: str, judge: _FakeJudge) -> Any:
    return execute_test(
        test_case=_make_case(),
        category="code_generation",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_FakeMutRunner(mut_output),
        judge_runner=_make_runner(judge),
        system_prompt=None,
        judge_system_prompt=None,
    )


def test_empty_response_does_not_call_the_judge() -> None:
    judge = _FakeJudge()

    output, passed = _make_runner(judge).evaluate_test(
        _make_case(), "code_generation", "the prompt", _EMPTY_RESPONSE, None
    )

    assert judge.calls == []
    assert passed is False
    assert output["error_kind"] == "sanity_no_code"
    assert output["overall_score"] == 0.0
    assert output["judge_score"] == 0.0
    assert output["final_score"] == 0.0
    assert [finding["check"] for finding in output["sanity_findings"]] == ["sanity_no_code"]
    assert all(finding["severity"] == "hard" for finding in output["sanity_findings"])


def test_prose_response_reaches_the_judge_with_an_advisory_finding() -> None:
    """Code-free but non-empty output is advisory — never an automatic zero."""
    judge = _FakeJudge()

    output, passed = _make_runner(judge).evaluate_test(
        _make_case(), "code_generation", "the prompt", _NO_CODE_RESPONSE, None
    )

    assert len(judge.calls) == 1
    assert passed is True
    assert output["overall_score"] == 0.9

    findings = output["sanity_findings"]
    assert [finding["check"] for finding in findings] == ["sanity_no_code"]
    assert findings[0]["severity"] == "advisory"


def test_truncated_response_does_not_call_the_judge() -> None:
    judge = _FakeJudge()

    output, passed = _make_runner(judge).evaluate_test(
        _make_case(), "code_generation", "the prompt", "```python\nprint('never closed", None
    )

    assert judge.calls == []
    assert passed is False
    assert output["error_kind"] == "sanity_truncated"


def test_unfenced_code_response_still_calls_the_judge() -> None:
    """Unfenced code is advisory — the judge scores it like any other response."""
    judge = _FakeJudge()

    output, passed = _make_runner(judge).evaluate_test(
        _make_case(), "code_generation", "the prompt", _UNFENCED_CODE_RESPONSE, None
    )

    assert len(judge.calls) == 1
    assert passed is True
    assert output["overall_score"] == 0.9

    findings = output["sanity_findings"]
    assert [finding["check"] for finding in findings] == ["sanity_unfenced_code"]
    assert findings[0]["severity"] == "advisory"


def test_valid_response_calls_the_judge_and_attaches_advisory_findings() -> None:
    judge = _FakeJudge()

    output, passed = _make_runner(judge).evaluate_test(
        _make_case(), "code_generation", "the prompt", _ADVISORY_RESPONSE, None
    )

    assert len(judge.calls) == 1
    assert passed is True
    assert output["overall_score"] == 0.9

    findings = output["sanity_findings"]
    assert [finding["check"] for finding in findings] == ["sanity_no_declared_files"]
    assert findings[0]["severity"] == "advisory"


def test_clean_response_attaches_an_empty_findings_list() -> None:
    judge = _FakeJudge()

    output, _ = _make_runner(judge).evaluate_test(
        _make_case(), "code_generation", "the prompt", _VALID_RESPONSE, None
    )

    assert len(judge.calls) == 1
    assert output["sanity_findings"] == []


def test_code_sanity_checks_disabled_skips_the_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nichebench.execution.runners.judge.get_config",
        lambda: _StubConfig(False),
    )
    judge = _FakeJudge()

    output, passed = _make_runner(judge).evaluate_test(
        _make_case(), "code_generation", "the prompt", _NO_CODE_RESPONSE, None
    )

    # The judge is reached even for a no-code response, and nothing is attached.
    assert len(judge.calls) == 1
    assert passed is True
    assert output == _JUDGE_OUTPUT
    assert "sanity_findings" not in output


def test_effective_evaluation_config_disables_the_sanity_layer() -> None:
    """The runner's effective (profile-merged) block wins over the global config.

    ``run.py`` merges the active profile into ``executor.evaluation_config``
    — the dict handed to the judge — before any test executes, so
    ``profiles.<name>.evaluation.code_sanity_checks`` has to be the switch
    the judge reads, not an unprofiled global read.
    """
    judge = _FakeJudge()
    runner = _make_runner(judge)
    runner.evaluation_config = {"code_sanity_checks": False}

    output, passed = runner.evaluate_test(
        _make_case(), "code_generation", "the prompt", _NO_CODE_RESPONSE, None
    )

    # The global config says "on", the effective block says "off": off wins.
    assert len(judge.calls) == 1
    assert passed is True
    assert output == _JUDGE_OUTPUT
    assert "sanity_findings" not in output


def test_effective_evaluation_config_enables_the_sanity_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The effective block also wins in the other direction."""
    monkeypatch.setattr(
        "nichebench.execution.runners.judge.get_config",
        lambda: _StubConfig(False),
    )
    judge = _FakeJudge()
    runner = _make_runner(judge)
    runner.evaluation_config = {"code_sanity_checks": True}

    output, _ = runner.evaluate_test(
        _make_case(), "code_generation", "the prompt", _EMPTY_RESPONSE, None
    )

    assert judge.calls == []
    assert output["error_kind"] == "sanity_no_code"


def test_hard_sanity_failure_reaches_the_result() -> None:
    result = _execute(_EMPTY_RESPONSE, _FakeJudge())

    assert result.passed is False
    assert result.error_kind == "sanity_no_code"
    assert result.judge_output["overall_score"] == 0.0
    # The MUT output stays untouched for forensics.
    assert result.mut_output == _EMPTY_RESPONSE

    payload = result.to_dict()
    assert payload["error_kind"] == "sanity_no_code"
    assert payload["judge_score"] == 0.0
    assert payload["final_score"] == 0.0
    assert [finding["check"] for finding in payload["sanity_findings"]] == ["sanity_no_code"]


def test_result_to_dict_includes_sanity_findings() -> None:
    result = _execute(_ADVISORY_RESPONSE, _FakeJudge())

    assert result.passed is True
    payload = result.to_dict()

    findings = payload["sanity_findings"]
    assert [finding["check"] for finding in findings] == ["sanity_no_declared_files"]
    assert findings[0]["severity"] == "advisory"
    assert findings[0]["message"]
