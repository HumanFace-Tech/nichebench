from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from nichebench.metrics.deepeval_quiz_metric import DeepEvalQuizMetric


class MockJudge:
    def score_quiz(self, *, question, choices, gold, candidate, model="mock", model_params=None, system_prompt=None):
        # Simple mock: extract letter and compare
        selected = None
        import re

        m = re.search(r"\b([A-E])\b", candidate or "")
        if m:
            selected = m.group(1)
        ok = selected == (gold or "")
        return {"pass": ok, "selected": selected or "", "score": 1 if ok else 0, "explanation": "mocked"}


def test_deepeval_quiz_metric_integration():
    # Build a lightweight LLMTestCase expected by deepeval
    tc = LLMTestCase(input="Which is correct?", actual_output="B", expected_output="B")
    metric = DeepEvalQuizMetric(judge=MockJudge(), model="mock")
    # assert_test will raise if metric fails
    assert_test(tc, [metric])
