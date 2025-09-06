"""DeepEval-compatible quiz metric that wraps our Litellm judge.

This keeps full compatibility with deepeval's Metric interface while
delegating the actual LLM-as-judge work to our existing adapter.
"""

from __future__ import annotations

from typing import Optional

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import LiteLLMJudge


class DeepEvalQuizMetric(BaseMetric):
    """A minimal deepeval BaseMetric implementation for MCQ-style quizzes.

    It expects the test case to provide `input` (question), `actual_output`
    (model-under-test answer), and `expected_output` (gold letter, e.g. 'A').
    """

    def __init__(
        self,
        judge: Optional[LiteLLMJudge] = None,
        model: Optional[str] = "openai/gpt-5",
        judge_model: Optional[str] = None,
        judge_params: Optional[dict] = None,
        threshold: float = 1.0,
        include_reason: bool = True,
        async_mode: bool = False,
    ):
        # Lazy import guard - keep ctor cheap
        self.threshold = threshold
        self.model = model
        # Which model to use for the judge LLM (if provided). If None we reuse
        # `model` as a sensible default but keep the param explicit.
        self.judge_model = judge_model or model
        self.judge_params = judge_params or {}
        self.include_reason = include_reason
        self.async_mode = async_mode
        self.error: Optional[str] = None
        self.score: float = 0.0
        self.reason: str = ""
        self.success: bool = False
        self.last_judge_response: Optional[dict] = None
        # allow injecting a test/mock judge
        if judge is not None:
            self.judge = judge
        else:
            client = LiteLLMClient()
            self.judge = LiteLLMJudge(client)

    def measure(self, test_case: LLMTestCase) -> float:
        try:
            question = getattr(test_case, "input", "") or ""
            actual = getattr(test_case, "actual_output", "") or ""
            expected = getattr(test_case, "expected_output", "") or ""
            # We don't require explicit choices in the test case; pass empty list.
            # Forward judge_model and allow frameworks to provide a custom
            # judge_system_prompt via the test_case if present (see runner).
            judge_system_prompt = getattr(test_case, "judge_system_prompt", None)
            judge_notes = getattr(test_case, "judge_notes", None)
            res = self.judge.score_quiz(
                question=question,
                choices=[],
                gold=expected,
                candidate=actual,
                model=self.judge_model,
                model_params=self.judge_params,
                system_prompt=judge_system_prompt,
                judge_notes=judge_notes,
            )
            passed = bool(res.get("pass", False))
            self.score = 1.0 if passed else 0.0
            self.last_judge_response = res
            if self.include_reason:
                self.reason = str(res.get("explanation", res.get("raw", "")))
            self.success = self.score >= self.threshold
            return self.score
        except Exception as e:
            self.error = str(e)
            self.success = False
            raise

    async def a_measure(self, test_case: LLMTestCase, **kwargs) -> float:
        # synchronous fallback for simplicity
        return self.measure(test_case)

    def is_successful(self) -> bool:
        if self.error is not None:
            self.success = False
        return self.success

    @property
    def __name__(self) -> str:  # readable name in deepeval reports
        return "DeepEval Quiz Metric"
