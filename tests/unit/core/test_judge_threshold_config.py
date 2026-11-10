"""``evaluation.judge_threshold`` flows from the config into the judge verdict.

Regression coverage for the documented-but-ignored sample-config key:

* ``NicheBenchConfig.get_evaluation_config(profile=...)`` carries the key for
  the effective run — covered in ``tests/unit/config/test_config.py``;
* ``TestExecutor`` hands that same dict to the judge runner, so the profile
  ``run.py`` merges in after construction is what the judge reads;
* ``JudgeRunner`` compares ``overall_score`` against it and falls back to the
  environment-backed ``settings.judge_threshold`` when the run carries no
  usable value.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

from nichebench.config.settings import settings
from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.orchestrator import TestExecutor
from nichebench.execution.runners.judge import JudgeRunner

# Structurally valid MUT response (declared path + fenced block) so the C4
# sanity gate never short-circuits the threshold decision.
_VALID_MUT = (
    "FILENAME: src/solution.py\n"
    "\n"
    "```python\n"
    "print('ok')\n"
    "```\n"
)

_UNSET = object()


class _ScoredJudge:
    """Judge stand-in returning one fixed ``overall_score``."""

    def __init__(self, score: float) -> None:
        self.score = score

    def _verdict(self) -> Dict[str, Any]:
        return {"criteria": [], "overall_score": self.score, "summary": "", "raw": ""}

    def score_code_generation(self, **kwargs: Any) -> Dict[str, Any]:
        return self._verdict()

    def score_bug_fixing(self, **kwargs: Any) -> Dict[str, Any]:
        return self._verdict()


def _make_runner(score: float, evaluation_config: Any = _UNSET) -> JudgeRunner:
    """Build a judge runner without ``__init__``, like the other judge tests.

    ``evaluation_config`` is left unset entirely when not given, so the
    fallback path (no attribute at all) is covered too.
    """
    runner = JudgeRunner.__new__(JudgeRunner)
    runner.model_str = "openai/gpt-4o"
    runner.model_config = {"parameters": {}}
    runner.client = None
    runner.judge = _ScoredJudge(score)  # type: ignore[assignment]
    if evaluation_config is not _UNSET:
        runner.evaluation_config = evaluation_config
    return runner


def _make_case() -> TestCaseSpec:
    return TestCaseSpec(id="code_001", type="code_generation", raw={"prompt": "task"}, checklist=["criterion"])


def _passed(runner: JudgeRunner) -> bool:
    _, passed = runner.evaluate_test(_make_case(), "code_generation", "the prompt", _VALID_MUT, None)
    return passed


def test_a_configured_threshold_decides_the_verdict() -> None:
    """The same 0.75 verdict fails at 0.8 and passes at 0.7."""
    assert _passed(_make_runner(0.75, {"judge_threshold": 0.7})) is True
    assert _passed(_make_runner(0.75, {"judge_threshold": 0.8})) is False


def test_without_an_evaluation_config_the_environment_setting_decides() -> None:
    """A runner built without the config keeps the pre-existing behaviour."""
    runner = _make_runner(settings.judge_threshold)
    assert _passed(runner) is True


def test_an_unusable_configured_threshold_falls_back_to_the_setting() -> None:
    """A malformed configured value must not break scoring."""
    runner = _make_runner(settings.judge_threshold, {"judge_threshold": "bogus"})
    assert _passed(runner) is True


def test_the_executor_hands_the_effective_evaluation_config_to_the_judge() -> None:
    """The judge reads the executor's own evaluation dict, by reference.

    ``run.py`` merges the active profile into ``executor.evaluation_config``
    after construction and before any test executes, so a fresh unprofiled
    copy inside the judge would silently drop ``evaluation.judge_threshold``.
    """
    mut_cfg = {"provider": "groq", "model": "test-model", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

    with (
        patch("nichebench.execution.orchestrator.get_config") as mock_config,
        patch.object(TestExecutor, "_load_system_prompt", return_value=None),
        patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
    ):
        mock_config.return_value.get_evaluation_config.return_value = {"judge_threshold": 0.85}
        mock_config.return_value.get_model_string.side_effect = lambda cfg: f"{cfg['provider']}/{cfg['model']}"
        executor = TestExecutor(
            framework="drupal",
            category="code_generation",
            mut_config=mut_cfg,
            judge_config=judge_cfg,
            network_config=network_cfg,
        )

    # What ``run.py`` does with the profile-merged evaluation config.
    executor.evaluation_config.update({"judge_threshold": 0.9})

    assert executor.judge_runner.evaluation_config is executor.evaluation_config
    assert executor.judge_runner._judge_threshold() == 0.9
