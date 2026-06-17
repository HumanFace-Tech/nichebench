"""Judge runner: drives LLM-as-a-judge evaluation of MUT output.

This module is the bridge between the benchmark executor and the judge
provider (:class:`nichebench.providers.litellm_judge.LiteLLMJudge`).

Responsibilities
================
* Accept a :class:`TestCaseSpec` and the MUT output, then route to the
  appropriate judge metric (quiz, code generation, bug fixing, or runtime).
* For runtime tasks, assemble a human-readable *artifact summary* from
  the raw runtime output bundle (``final.diff``, ``run.log``,
  ``checks.json``, ``phpcs.json``, ``phpstan.json``) so the judge LLM
  can reason over the full execution trace.
* Return a structured verdict (``judge_output`` dict) and a boolean
  ``passed`` flag that the scoring layer combines with deterministic
  checks.

Key boundaries
=============
* This runner knows about category (quiz / code_generation / bug_fixing /
  runtime) but does **not** implement scoring weights — those belong to
  :mod:`nichebench.core.scoring`.
* The actual judge LLM calls are delegated to :class:`LiteLLMJudge` via
  DeepEval metric wrappers.  Changing the judge provider means updating
  that module, not this one.
* Artifact summarisation is lossy (diff/log truncation at character
  limits); this is intentional to keep prompt size bounded for the judge.
"""

from typing import Any, Dict, Optional, Tuple

from deepeval.test_case import LLMTestCase

from nichebench.core.datamodel import TestCaseSpec
from nichebench.metrics.bug_fixing_metric import DeepEvalBugFixingMetric
from nichebench.metrics.code_generation_metric import DeepEvalCodeGenerationMetric
from nichebench.metrics.deepeval_quiz_metric import DeepEvalQuizMetric
from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import LiteLLMJudge


def _build_runtime_task_description(raw: Dict[str, Any]) -> str:
    """Pull title + structured description fields into a single readable block.

    Used for the ``runtime`` category to give the judge LLM a compact
    task overview when it cannot see the original ``TASK.md``.
    """
    parts: list[str] = []
    title = str(raw.get("title", "")).strip()
    if title:
        parts.append(f"Task: {title}")
    desc = raw.get("description_structured", {})
    if isinstance(desc, dict):
        bg = str(desc.get("background", "")).strip()
        if bg:
            parts.append(f"Background:\n{bg}")
        ac = desc.get("acceptance_criteria", [])
        if ac and isinstance(ac, list):
            parts.append("Acceptance Criteria:\n" + "\n".join(f"- {c}" for c in ac))
    return "\n\n".join(parts) if parts else "(no task description available)"


def _build_runtime_artifact_summary(
    artifacts: Dict[str, Any],
    max_diff_chars: int = 8000,
    max_log_chars: int = 4000,
) -> str:
    """Transform the raw artifact bundle into a compact text summary for the judge LLM.

    The summary includes (in order): git diff, agent run log, deterministic
    check results, PHPCS summary, and PHPStan summary.  Each section is
    truncated at the respective character limit to keep the judge prompt
    within size bounds.

    Args:
        artifacts: runtime output bundle — keys include ``final.diff``,
            ``run.log``, ``checks.json``, ``phpcs.json``, ``phpstan.json``.
        max_diff_chars: truncation threshold for the diff section.
        max_log_chars: truncation threshold for the log section (shows the
            *last* portion so recent activity is preserved).

    Returns:
        A multi-section text string, or ``"(no artifacts available)"`` if
        the bundle is empty.
    """
    parts: list[str] = []

    diff = artifacts.get("final.diff", "")
    if diff and isinstance(diff, str):
        if len(diff) > max_diff_chars:
            diff = diff[:max_diff_chars] + f"\n... [truncated at {max_diff_chars} chars]"
        parts.append(f"=== GIT DIFF (final.diff) ===\n{diff}")

    log = artifacts.get("run.log", "")
    if log and isinstance(log, str):
        if len(log) > max_log_chars:
            log = log[-max_log_chars:] + f"\n... [showing last {max_log_chars} chars]"
        parts.append(f"=== AGENT RUN LOG (run.log) ===\n{log}")

    checks = artifacts.get("checks.json", {})
    if checks and isinstance(checks, dict):
        det = checks.get("deterministic", [])
        if det and isinstance(det, list):
            lines = [
                f"  [{'PASS' if c.get('passed') else 'FAIL'}] " f"{c.get('name', '')}: {c.get('message', '')}"
                for c in det
            ]
            parts.append("=== DETERMINISTIC CHECKS ===\n" + "\n".join(lines))

    phpcs = artifacts.get("phpcs.json", {})
    if phpcs and isinstance(phpcs, dict):
        totals = phpcs.get("totals", {})
        parts.append(
            f"=== PHPCS ===\n"
            f"errors={totals.get('errors', '?')}, "
            f"warnings={totals.get('warnings', '?')}, "
            f"fixable={totals.get('fixable', '?')}"
        )

    phpstan = artifacts.get("phpstan.json", {})
    if phpstan and isinstance(phpstan, dict):
        totals = phpstan.get("totals", {})
        parts.append(
            f"=== PHPSTAN ===\n" f"error={totals.get('error', '?')}, " f"maybe_error={totals.get('maybe_error', '?')}"
        )

    return "\n\n".join(parts) if parts else "(no artifacts available)"


class JudgeRunner:
    """Coordinates judge evaluation for a single test case.

    Each instance owns a :class:`LiteLLMClient` (shared with the MUT
    runner at the executor level) and a :class:`LiteLLMJudge` adapter.

    Main entry point is :meth:`evaluate_test`, which:
      1. Builds a DeepEval :class:`LLMTestCase` from the MUT response.
      2. Selects the correct metric based on ``category``.
      3. Returns ``(judge_output, passed)`` for the scoring layer.

    For the ``runtime`` category, the runner builds an *artifact summary*
    (see :func:`_build_runtime_artifact_summary`) instead of passing
    raw MUT output directly, because the judge must evaluate a full
    agent run rather than a single-turn completion.
    """

    def __init__(
        self, model_str: str, model_config: Dict[str, Any], timeout: int, retry_attempts: int, retry_delay: int
    ):
        self.model_str = model_str
        self.model_config = model_config
        self.client = LiteLLMClient(timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay)
        self.judge = LiteLLMJudge(client=self.client)

    def evaluate_test(
        self,
        test_case: TestCaseSpec,
        category: str,
        user_input: str,
        mut_output: str,
        judge_system_prompt: Optional[str],
        runtime_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Evaluate MUT output using judge.

        Args:
            test_case: Test case specification
            category: Test category (quiz, code_generation, bug_fixing, runtime)
            user_input: User input/prompt
            mut_output: Model output to evaluate
            judge_system_prompt: Optional system prompt for judge
            runtime_artifacts: Optional dict of runtime artifacts for category='runtime'

        Returns:
            Tuple of (judge_output, passed)
        """
        # Create LLMTestCase for DeepEval compatibility
        stc = LLMTestCase(
            input=user_input,
            actual_output=mut_output,
            expected_output=test_case.correct_choice or "",
        )

        # Attach dynamic metadata via the metadata dict field.
        # (DeepEval ≥3.4 made LLMTestCase a strict pydantic model — no
        # free-form setattr — so we carry our extras on ``metadata``.)
        metadata: Dict[str, Any] = dict(stc.metadata) if stc.metadata else {}
        if hasattr(test_case, "checklist") and test_case.checklist:
            metadata["checklist"] = test_case.checklist
        if judge_system_prompt:
            metadata["judge_system_prompt"] = judge_system_prompt
        if hasattr(test_case, "context") and test_case.context:
            metadata["context"] = test_case.context
        if hasattr(test_case, "summary") and test_case.summary:
            metadata["summary"] = test_case.summary
        if test_case.raw.get("judge_notes"):
            metadata["judge_notes"] = test_case.raw["judge_notes"]
        stc.metadata = metadata

        # Create appropriate metric and evaluate
        judge_output: Dict[str, Any] = {}
        passed = False

        if category == "quiz":
            metric = DeepEvalQuizMetric(
                judge=self.judge,
                judge_model=self.model_str,
                judge_params=self.model_config.get("parameters", {}),
            )
            score = metric.measure(stc)
            passed = bool(score)

            if hasattr(metric, "last_judge_response") and metric.last_judge_response is not None:
                judge_output = metric.last_judge_response
            else:
                judge_output = {"score": score, "explanation": "No detailed explanation available"}

        elif category == "code_generation":
            metric = DeepEvalCodeGenerationMetric(
                judge=self.judge,
                judge_model=self.model_str,
                judge_params=self.model_config.get("parameters", {}),
            )
            score = metric.measure(stc)
            passed = bool(score >= 0.7)  # Default threshold

            if hasattr(metric, "last_judge_response") and metric.last_judge_response is not None:
                judge_output = metric.last_judge_response
            else:
                judge_output = {"score": score, "explanation": "No detailed explanation available"}

        elif category == "code_agent":
            # Use the same metric as code_generation since we're testing the same capabilities
            metric = DeepEvalCodeGenerationMetric(
                judge=self.judge,
                judge_model=self.model_str,
                judge_params=self.model_config.get("parameters", {}),
            )
            score = metric.measure(stc)
            passed = bool(score >= 0.7)  # Default threshold

            if hasattr(metric, "last_judge_response") and metric.last_judge_response is not None:
                judge_output = metric.last_judge_response
            else:
                judge_output = {"score": score, "explanation": "No detailed explanation available"}

        elif category == "bug_fixing":
            metric = DeepEvalBugFixingMetric(
                judge=self.judge,
                judge_model=self.model_str,
                judge_params=self.model_config.get("parameters", {}),
            )
            score = metric.measure(stc)
            passed = bool(score >= 0.7)  # Default threshold

            if hasattr(metric, "last_judge_response") and metric.last_judge_response is not None:
                judge_output = metric.last_judge_response
            else:
                judge_output = {"score": score, "explanation": "No detailed explanation available"}

        elif category == "runtime":
            # Runtime tasks use score_runtime with artifacts
            llm_judge_config = test_case.raw.get("llm_judge", {})
            checklist = llm_judge_config.get("checklist", [])

            if not checklist:
                # No checklist means perfect score by default
                judge_output = {"overall_score": 1.0, "criteria": [], "summary": "No checklist - passing by default"}
                passed = True
            else:
                # Call score_runtime with artifacts
                if runtime_artifacts:
                    task_desc = _build_runtime_task_description(test_case.raw)
                    artifact_summary = _build_runtime_artifact_summary(runtime_artifacts)

                    # Set the input for the judge
                    setattr(stc, "input", task_desc)
                    setattr(stc, "actual_output", artifact_summary)

                judge_output = self.judge.score_runtime(
                    task_description=str(getattr(stc, "input", user_input)),
                    artifact_summary=str(getattr(stc, "actual_output", mut_output)),
                    checklist_items=checklist,
                    model=self.model_str,
                    model_params=self.model_config.get("parameters", {}),
                    system_prompt=llm_judge_config.get("model_role", "Be a fair evaluator."),
                )

                overall_score = float(judge_output.get("overall_score", 0.0))
                passed = overall_score >= 0.5  # Default threshold

        else:
            # Fallback for unknown categories - never random (test requirement)
            judge_output = {
                "overall_score": 1.0,
                "explanation": "Unknown category - passing by default",
                "judge_system_prompt": bool(judge_system_prompt),
            }
            passed = True

        return judge_output, passed
