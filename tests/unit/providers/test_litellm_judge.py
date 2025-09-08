from types import SimpleNamespace

import pytest

from nichebench.providers.litellm_judge import LiteLLMJudge


class MockClient:
    def __init__(self, output: str):
        self._output = output

    def generate(self, prompt: str, model: str, model_params=None):
        # Return a dict matching LiteLLMClient.generate
        return {"model": model, "output": self._output}


def test_score_quiz_with_valid_json_response():
    """Test normal case with valid JSON from judge."""
    json_response = '{"pass": true, "selected": "A", "score": 1, "explanation": "Correct choice"}'
    client = MockClient(json_response)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(
        question="What is 2+2?", choices=["3", "4", "5"], gold="B", candidate="B", model="openai/gpt-5"
    )

    assert result["pass"] is True
    assert result["selected"] == "A"
    assert result["score"] == 1
    assert result["explanation"] == "Correct choice"
    assert "raw" in result
    assert result["raw"] == json_response


def test_score_quiz_with_json_in_code_fences():
    """Test JSON extraction from code fences."""
    fenced_response = '```json\n{"pass": false, "selected": "C", "score": 0, "explanation": "Wrong"}\n```'
    client = MockClient(fenced_response)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="C")

    assert result["pass"] is False
    assert result["selected"] == "C"
    assert result["score"] == 0


def test_score_quiz_with_extra_text_around_json():
    """Test JSON extraction when embedded in other text."""
    mixed_response = (
        "Looking at this question, my assessment is: "
        '{"pass": true, "selected": "B", "score": 1, "explanation": "Good reasoning"} '
        "Hope this helps!"
    )
    client = MockClient(mixed_response)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(question="Test", choices=["X", "Y"], gold="B", candidate="B")

    assert result["pass"] is True
    assert result["selected"] == "B"


def test_score_quiz_with_malformed_json():
    """Test conservative fallback when judge returns malformed JSON."""
    bad_json = '{"pass": true, "selected": "A", "score": 1, "explanation": "Missing closing brace"'
    client = MockClient(bad_json)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(question="Test", choices=["X"], gold="A", candidate="A")

    # Should fall back to conservative failure
    assert result["pass"] is False
    assert result["score"] == 0
    assert "Judge did not return structured JSON" in result["explanation"]
    assert result["raw"] == bad_json


def test_score_quiz_with_plain_text_response():
    """Test fallback when judge returns plain text instead of JSON."""
    text_response = "I think the answer is A but here's some reasoning about why"
    client = MockClient(text_response)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="B")

    assert result["pass"] is False
    assert result["score"] == 0
    assert result["selected"] == ""
    assert "Judge did not return structured JSON" in result["explanation"]


def test_score_quiz_with_system_prompt_injection():
    """Test that system prompts are properly included in judge prompts."""
    json_response = '{"pass": true, "selected": "A", "score": 1, "explanation": "Good"}'

    class TrackingMockClient:
        def __init__(self, output: str):
            self._output = output
            self.last_prompt: str = ""

        def generate(self, prompt: str, model: str, model_params=None):
            self.last_prompt = prompt
            return {"model": model, "output": self._output}

    client = TrackingMockClient(json_response)
    judge = LiteLLMJudge(client=client)

    system_prompt = "You are a specialized Drupal expert judge."
    result = judge.score_quiz(
        question="What is Drupal?", choices=["CMS", "Framework"], gold="A", candidate="A", system_prompt=system_prompt
    )

    # Verify system prompt was included
    assert system_prompt in client.last_prompt
    assert "You are an evaluation judge." in client.last_prompt
    assert result["pass"] is True


def test_score_quiz_prompt_structure():
    """Test that quiz prompts include all necessary components."""

    class PromptCapturingClient:
        def __init__(self):
            self.captured_prompt: str = ""

        def generate(self, prompt: str, model: str, model_params=None):
            self.captured_prompt = prompt
            return {
                "model": model,
                "output": '{"pass": true, "selected": "A", "score": 1, "explanation": "Test"}',
            }

    client = PromptCapturingClient()
    judge = LiteLLMJudge(client=client)

    judge.score_quiz(
        question="What is the capital of France?",
        choices=["London", "Paris", "Berlin"],
        gold="B",
        candidate="The capital is Paris",
    )

    prompt = client.captured_prompt
    assert "Question: What is the capital of France?" in prompt
    assert "A. London" in prompt
    assert "B. Paris" in prompt
    assert "C. Berlin" in prompt
    assert "Gold (correct answer): B" in prompt
    assert "Model answer: The capital is Paris" in prompt
    assert "Return a JSON object" in prompt
