from nichebench.metrics.quiz_metric import QuizMetric
from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import LiteLLMJudge


class MockClient(LiteLLMClient):
    def generate(self, prompt: str, model: str = "openai/gpt-5", *, model_params=None, **kwargs):
        # Return a JSON blob as the judge would
        return {"model": model, "output": '{"pass": true, "selected": "B", "score": 1, "explanation": "Good choice"}'}


def test_quiz_metric_scores_correctly():
    client = MockClient()
    judge = LiteLLMJudge(client)
    metric = QuizMetric(judge)

    out = metric.score(
        task_id="drupal_quiz_001",
        question="Which option is correct?",
        choices=["one", "two", "three"],
        gold="B",
        candidate="B - because it's correct",
        judge_model="mock",
    )

    assert out["id"] == "drupal_quiz_001"
    assert out["pass"] is True
    assert out["selected"] == "B"
    assert out["score"] == 1
