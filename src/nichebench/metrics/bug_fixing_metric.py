"""DeepEval-compatible bug fixing metric that uses LLM judge with checklist evaluation.

This metric evaluates bug fixing tasks by using an LLM judge to assess
the proposed fix against a checklist of criteria. The judge returns structured
JSON with per-criterion pass/fail results.
"""

from __future__ import annotations

from typing import Any, Optional

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import LiteLLMJudge


class DeepEvalBugFixingMetric(BaseMetric):
    """A deepeval BaseMetric implementation for bug fixing tasks.

    It expects the test case to provide `input` (bug description/prompt),
    `actual_output` (model-proposed fix), and optionally a `checklist`
    via test case attributes for structured evaluation.
    """

    def __init__(
        self,
        judge: Any = None,  # Allow any judge for testing
        model: Optional[str] = "openai/gpt-4o",
        judge_model: Optional[str] = None,
        judge_params: Optional[dict] = None,
        threshold: float = 0.7,
        include_reason: bool = True,
        async_mode: bool = False,
    ):
        self.threshold = threshold
        self.model = model
        self.judge_model = judge_model or model
        self.judge_params = judge_params or {}
        self.include_reason = include_reason
        self.async_mode = async_mode
        self.error: Optional[str] = None
        self.score: Optional[float] = 0.0
        self.reason: Optional[str] = ""
        self.success: Optional[bool] = False
        self.last_judge_response: Optional[dict] = None

        # Allow injecting a test/mock judge
        if judge is not None:
            self.judge = judge
        else:
            client = LiteLLMClient()
            self.judge = LiteLLMJudge(client)

    def measure(self, test_case: LLMTestCase) -> float:
        try:
            # Extract test case data
            bug_description = getattr(test_case, "input", "") or ""
            proposed_fix = getattr(test_case, "actual_output", "") or ""
            checklist = getattr(test_case, "checklist", [])
            judge_system_prompt = getattr(test_case, "judge_system_prompt", None)
            judge_notes = getattr(test_case, "judge_notes", None)

            # Use the score_bug_fixing method
            res = self.judge.score_bug_fixing(
                bug_description=bug_description,
                proposed_fix=proposed_fix,
                checklist=checklist,
                model=self.judge_model or "openai/gpt-4o",
                model_params=self.judge_params,
                system_prompt=judge_system_prompt,
                judge_notes=judge_notes,
            )

            # Extract overall score
            self.score = float(res.get("overall_score", 0.0))
            self.last_judge_response = res

            if self.include_reason:
                self.reason = str(res.get("summary", res.get("raw", "")))

            self.success = self.score >= self.threshold
            return self.score

        except Exception as e:
            self.error = str(e)
            self.success = False
            raise

    async def a_measure(self, test_case: LLMTestCase, **kwargs) -> float:
        # Synchronous fallback for simplicity
        return self.measure(test_case)

    def is_successful(self) -> bool:
        if self.error is not None:
            self.success = False
        return bool(self.success)
