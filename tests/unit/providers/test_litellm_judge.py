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


# ---- score_runtime tests ----


def test_score_runtime_valid_response_weighted():
    """Happy path: judge returns well-formed JSON with criterion_id keys; score is weighted."""
    checklist = [
        {"id": "entity_defined", "question": "Is the entity defined?", "weight": 0.6},
        {"id": "routing_yml", "question": "Is routing.yml present?", "weight": 0.4},
    ]
    json_resp = (
        '{"criteria": ['
        '{"criterion_id": "entity_defined", "pass": true, "explanation": "found"},'
        '{"criterion_id": "routing_yml", "pass": false, "explanation": "missing"}'
        '], "overall_score": 0.6, "summary": "Partial work"}'
    )
    client = MockClient(json_resp)
    judge = LiteLLMJudge(client=client)

    result = judge.score_runtime(
        task_description="Build an entity",
        artifact_summary="diff content here",
        checklist_items=checklist,
        model="openai/gpt-4o",
    )

    assert result["overall_score"] == 0.6
    assert len(result["criteria"]) == 2
    assert "summary" in result
    assert "raw" in result


def test_score_runtime_recomputes_weighted_score_when_missing():
    """If judge omits overall_score, it is computed from weighted criteria."""
    checklist = [
        {"id": "a", "question": "Q1?", "weight": 0.7},
        {"id": "b", "question": "Q2?", "weight": 0.3},
    ]
    # Judge omits overall_score
    json_resp = (
        '{"criteria": ['
        '{"criterion_id": "a", "pass": true, "explanation": "ok"},'
        '{"criterion_id": "b", "pass": "partial", "explanation": "half done"}'
        '], "summary": "ok"}'
    )
    client = MockClient(json_resp)
    judge = LiteLLMJudge(client=client)

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=checklist,
    )
    # a=true → 0.7, b=partial → 0.3*0.5=0.15; total_weight=1.0 → 0.85
    assert abs(result["overall_score"] - 0.85) < 0.001


def test_score_runtime_invalid_json_returns_zero():
    """Invalid JSON from judge → overall_score is 0.0, no randomness."""
    checklist = [{"id": "x", "question": "Q?", "weight": 1.0}]
    client = MockClient("not json at all")
    judge = LiteLLMJudge(client=client)

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=checklist,
    )
    assert result["overall_score"] == 0.0
    assert result["criteria"][0]["pass"] is False
    assert "raw" in result


def test_score_runtime_no_checklist_returns_one():
    """Empty checklist → deterministic 1.0, no judge call needed."""
    client = MockClient("{}")
    judge = LiteLLMJudge(client=client)

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=[],
    )
    assert result["overall_score"] == 1.0
    assert result["criteria"] == []


def test_score_runtime_positional_fallback():
    """When criterion_ids are unrecognised, positional fallback is used."""
    checklist = [
        {"id": "real_id_1", "question": "Q1?", "weight": 0.5},
        {"id": "real_id_2", "question": "Q2?", "weight": 0.5},
    ]
    # Judge returns wrong/missing criterion_ids — triggers positional fallback
    json_resp = (
        '{"criteria": ['
        '{"criterion_id": "wrong_id_a", "pass": true, "explanation": "ok"},'
        '{"criterion_id": "wrong_id_b", "pass": false, "explanation": "missing"}'
        '], "summary": "partial"}'
    )
    client = MockClient(json_resp)
    judge = LiteLLMJudge(client=client)

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=checklist,
    )
    # Positional: index 0 → weight 0.5 (true), index 1 → weight 0.5 (false) → 0.5
    assert abs(result["overall_score"] - 0.5) < 0.001


def test_score_runtime_prompt_includes_all_sections():
    """Prompt must include task description, artifacts, and checklist."""

    class CapturingClient:
        def __init__(self):
            self.last_prompt = ""

        def generate(self, prompt, model, model_params=None):
            self.last_prompt = prompt
            return {"output": '{"criteria": [], "overall_score": 1.0, "summary": "ok"}'}

    client = CapturingClient()
    judge = LiteLLMJudge(client=client)

    judge.score_runtime(
        task_description="Build a wizard",
        artifact_summary="=== GIT DIFF ===\n+++ some diff",
        checklist_items=[{"id": "check_1", "question": "Is wizard built?", "weight": 1.0}],
        system_prompt="You are strict.",
    )

    prompt = client.last_prompt
    assert "Build a wizard" in prompt
    assert "GIT DIFF" in prompt
    assert "check_1" in prompt
    assert "Is wizard built?" in prompt
    assert "You are strict." in prompt


def test_score_runtime_api_error_raises():
    """Client API errors (model not found, network) must propagate as RuntimeError.

    This lets the executor's outer except-block leave judge_score=None so the
    run falls back to deterministic-only scoring instead of scoring the MUT as 0.
    """
    checklist = [{"id": "x", "question": "Q?", "weight": 1.0}]
    client = MockClient("[Error: LiteLLM error after 3 attempts: model_not_found]")
    judge = LiteLLMJudge(client=client)

    with pytest.raises(RuntimeError, match=r"\[Error:"):
        judge.score_runtime(
            task_description="Task",
            artifact_summary="diff",
            checklist_items=checklist,
        )
