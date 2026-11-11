import pytest

from nichebench.providers.litellm_judge import JudgeError, LiteLLMJudge


class MockClient:
    def __init__(self, output: str):
        self._output = output

    def generate(self, prompt: str, model: str, model_params=None):
        # Return a dict matching LiteLLMClient.generate
        return {"model": model, "output": self._output}


class ScriptedClient:
    """Client returning scripted outputs in order, then repeating the last.

    Counts every ``generate`` call so retry behaviour can be asserted.
    """

    def __init__(self, *outputs: str):
        self.outputs = list(outputs)
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, prompt: str, model: str, model_params=None):
        self.calls += 1
        self.prompts.append(prompt)
        output = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        return {"model": model, "output": output}


def test_score_quiz_with_valid_json_response():
    """Happy path: the judge verdict is normalised and recomputed against gold."""
    json_response = '{"pass": true, "selected": "B", "score": 1, "explanation": "Correct choice"}'
    client = ScriptedClient(json_response)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(
        question="What is 2+2?", choices=["3", "4", "5"], gold="B", candidate="B", model="openai/gpt-5"
    )

    assert result["pass"] is True
    assert result["selected"] == "B"
    assert result["score"] == 1.0
    assert result["overall_score"] == 1.0
    assert result["explanation"] == "Correct choice"
    assert result["judge_pass"] is True
    assert result["judge_pass_mismatch"] is False
    assert result["attempts"] == 1
    assert "raw" in result
    assert result["raw"] == json_response
    assert client.calls == 1


def test_score_quiz_judge_pass_boolean_is_audit_only():
    """A judge boolean that disagrees with its own ``selected`` never wins."""
    response = '{"pass": true, "selected": "A", "score": 1, "explanation": "claims correct"}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(question="Q?", choices=["x", "y"], gold="B", candidate="A")

    assert result["selected"] == "A"
    assert result["pass"] is False  # selected A != gold B
    assert result["score"] == 0.0
    assert result["overall_score"] == 0.0
    assert result["judge_pass"] is True
    assert result["judge_pass_mismatch"] is True
    # A valid but wrong answer is a legitimate verdict — never retried.
    assert client.calls == 1


def test_score_quiz_with_json_in_code_fences():
    """Test JSON extraction from code fences."""
    fenced_response = '```json\n{"pass": false, "selected": "C", "score": 0, "explanation": "Wrong"}\n```'
    client = ScriptedClient(fenced_response)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(question="Test", choices=["A", "B", "C"], gold="A", candidate="C")

    assert result["pass"] is False
    assert result["selected"] == "C"
    assert result["score"] == 0.0
    assert result["attempts"] == 1


def test_score_quiz_with_extra_text_around_json():
    """Test JSON extraction when embedded in other text."""
    mixed_response = (
        "Looking at this question, my assessment is: "
        '{"pass": true, "selected": "B", "score": 1, "explanation": "Good reasoning"} '
        "Hope this helps!"
    )
    client = ScriptedClient(mixed_response)
    judge = LiteLLMJudge(client=client)

    result = judge.score_quiz(question="Test", choices=["X", "Y"], gold="B", candidate="B")

    assert result["pass"] is True
    assert result["selected"] == "B"


def test_score_quiz_with_malformed_json():
    """Malformed JSON is retried, then raised as ``judge_invalid_json``."""
    bad_json = '{"pass": true, "selected": "A", "score": 1, "explanation": "Missing closing brace"'
    client = ScriptedClient(bad_json)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["X", "Y"], gold="A", candidate="A")

    assert exc_info.value.kind == "judge_invalid_json"
    assert exc_info.value.attempts == 3
    assert client.calls == 3
    assert exc_info.value.last_raw == bad_json


def test_score_quiz_with_plain_text_response():
    """Plain text instead of JSON is retried, then raised as ``judge_invalid_json``."""
    text_response = "I think the answer is A but here's some reasoning about why"
    client = ScriptedClient(text_response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="B")

    assert exc_info.value.kind == "judge_invalid_json"
    assert exc_info.value.attempts == 3
    assert client.calls == 3


def test_score_quiz_api_error_raises_judge_unavailable():
    """``[Error: ...]`` payloads are retried, then raised as ``judge_unavailable``."""
    client = ScriptedClient("[Error: LiteLLM error after 3 attempts: model_not_found]")
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="A")

    assert exc_info.value.kind == "judge_unavailable"
    assert exc_info.value.attempts == 3
    assert client.calls == 3


def test_score_quiz_missing_selected_raises_judge_missing_selected():
    """A verdict without a usable ``selected`` is retried, then raised."""
    response = '{"pass": false, "score": 0, "explanation": "forgot to pick"}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="A")

    assert exc_info.value.kind == "judge_missing_selected"
    assert exc_info.value.attempts == 3
    assert client.calls == 3


def test_score_quiz_explicit_null_selected_is_a_scored_abstention():
    """``"selected": null`` with a verdict and an explanation is a scored zero."""
    response = '{"pass": false, "selected": null, "score": 0, "explanation": "no identifiable answer"}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    result = judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="A")

    assert result["status"] == "abstained"
    assert result["selected"] is None
    assert result["pass"] is False
    assert result["score"] == 0.0
    assert result["overall_score"] == 0.0
    assert result["explanation"] == "no identifiable answer"
    assert result["judge_pass"] is False
    assert result["attempts"] == 1
    assert result["raw"] == response
    # An abstention is a model failure — never retried.
    assert client.calls == 1


def test_score_quiz_external_status_never_overrides_a_valid_selection():
    """A judge-supplied ``status`` cannot turn a valid selection into an abstention."""
    response = '{"selected":"A","pass":true,"explanation":"correct","status":"abstained"}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    result = judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="A")

    assert result["selected"] == "A"
    assert result["pass"] is True
    assert result["score"] == 1.0
    assert result["overall_score"] == 1.0
    # The external status is dropped; no abstention marker is set.
    assert result.get("status") is None
    assert client.calls == 1


def test_score_quiz_external_status_is_ignored_when_scoring_a_wrong_answer():
    """``status: "abstained"`` with a real (wrong) selection scores the selection."""
    response = '{"selected":"B","pass":false,"explanation":"claims B","status":"abstained"}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    result = judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="B")

    assert result["selected"] == "B"
    assert result["pass"] is False
    assert result["score"] == 0.0
    assert result.get("status") is None
    assert client.calls == 1


def test_score_quiz_null_selected_with_pass_true_is_retried():
    """``selected: null`` with ``pass: true`` is not an abstention — it is retried."""
    response = '{"pass": true, "selected": null, "score": 1, "explanation": "no identifiable answer"}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="A")

    assert exc_info.value.kind == "judge_missing_selected"
    assert client.calls == 3


def test_score_quiz_abstention_status_is_set_by_the_adapter_not_the_judge():
    """A null selection with ``pass: false`` is an abstention regardless of ``status``."""
    response = (
        '{"pass": false, "selected": null, "score": 0, '
        '"explanation": "ambiguous", "status": "answered"}'
    )
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    result = judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="A")

    assert result["status"] == "abstained"
    assert result["selected"] is None
    assert result["score"] == 0.0
    assert client.calls == 1


def test_score_quiz_null_selected_without_explanation_is_retried():
    """A bare ``null`` without an explanation is not an abstention."""
    response = '{"pass": false, "selected": null, "score": 0}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="A")

    assert exc_info.value.kind == "judge_missing_selected"
    assert client.calls == 3


def test_score_quiz_null_selected_without_boolean_pass_is_retried():
    """A bare ``null`` without a boolean verdict is not an abstention."""
    response = '{"selected": null, "explanation": "no identifiable answer"}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["A", "B"], gold="A", candidate="A")

    assert exc_info.value.kind == "judge_missing_selected"
    assert client.calls == 3


@pytest.mark.parametrize("selected", ["", "   ", "Paris", "AB"])
def test_score_quiz_unusable_selected_raises_judge_missing_selected(selected):
    """Blank or non-letter ``selected`` values never count as a verdict."""
    response = f'{{"pass": true, "selected": "{selected}", "score": 1}}'
    judge = LiteLLMJudge(client=ScriptedClient(response), sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["A", "B", "C"], gold="A", candidate="A")

    assert exc_info.value.kind == "judge_missing_selected"


def test_score_quiz_out_of_range_selected_raises_judge_out_of_range():
    """A ``selected`` letter the quiz does not offer is retried, then raised."""
    response = '{"pass": true, "selected": "Z", "score": 1, "explanation": "Z"}'
    client = ScriptedClient(response)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_quiz(question="Test", choices=["A", "B", "C", "D"], gold="A", candidate="A")

    assert exc_info.value.kind == "judge_out_of_range"
    assert exc_info.value.attempts == 3
    assert client.calls == 3


def test_score_quiz_out_of_range_then_valid_retries_once():
    """One unusable attempt then a valid one → 2 attempts, success, one sleep."""
    sleeps: list[float] = []
    client = ScriptedClient(
        '{"pass": true, "selected": "Z", "score": 1, "explanation": "Z"}',
        '{"pass": true, "selected": "B", "score": 1, "explanation": "B"}',
    )
    judge = LiteLLMJudge(client=client, sleeper=sleeps.append, retry_delay=0.5)

    result = judge.score_quiz(question="Test", choices=["A", "B", "C", "D"], gold="B", candidate="B")

    assert result["pass"] is True
    assert result["selected"] == "B"
    assert result["attempts"] == 2
    assert client.calls == 2
    # Exponential backoff: one sleep of retry_delay * 2 ** 0 before attempt 2.
    assert sleeps == [0.5]


def test_score_quiz_lower_case_selected_is_normalised():
    """``selected`` is normalised to a single upper-case letter before comparison."""
    response = '{"pass": true, "selected": " b ", "score": 1, "explanation": "lower case"}'
    result = LiteLLMJudge(client=ScriptedClient(response)).score_quiz(
        question="Test", choices=["A", "B"], gold="B", candidate="B"
    )

    assert result["selected"] == "B"
    assert result["pass"] is True
    assert result["score"] == 1.0


def test_score_quiz_with_system_prompt_injection():
    """Test that system prompts are properly included in judge prompts."""
    json_response = '{"pass": true, "selected": "A", "score": 1, "explanation": "Good"}'

    client = ScriptedClient(json_response)
    judge = LiteLLMJudge(client=client)

    system_prompt = "You are a specialized Drupal expert judge."
    result = judge.score_quiz(
        question="What is Drupal?", choices=["CMS", "Framework"], gold="A", candidate="A", system_prompt=system_prompt
    )

    # Verify system prompt was included
    assert system_prompt in client.prompts[0]
    assert "You are an evaluation judge." in client.prompts[0]
    assert result["pass"] is True


def test_score_quiz_prompt_structure():
    """Test that quiz prompts include all necessary components."""

    client = ScriptedClient('{"pass": true, "selected": "B", "score": 1, "explanation": "Test"}')
    judge = LiteLLMJudge(client=client)

    judge.score_quiz(
        question="What is the capital of France?",
        choices=["London", "Paris", "Berlin"],
        gold="B",
        candidate="The capital is Paris",
    )

    prompt = client.prompts[0]
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


def test_score_runtime_invalid_json_raises_judge_invalid_json():
    """Invalid JSON from judge → retried, then JudgeError(judge_invalid_json).

    An unusable verdict is never silently scored as 0.0.
    """
    checklist = [{"id": "x", "question": "Q?", "weight": 1.0}]
    client = ScriptedClient("not json at all")
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as excinfo:
        judge.score_runtime(
            task_description="Task",
            artifact_summary="diff",
            checklist_items=checklist,
        )

    assert excinfo.value.kind == "judge_invalid_json"
    assert excinfo.value.attempts == 3
    assert client.calls == 3


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


def test_score_runtime_overall_score_is_recomputed_from_criteria():
    """A judge's own inflated ``overall_score`` never wins over the criteria."""
    checklist = [
        {"id": "a", "question": "Q1?", "weight": 1.0},
        {"id": "b", "question": "Q2?", "weight": 1.0},
    ]
    json_resp = (
        '{"criteria": ['
        '{"criterion_id": "a", "pass": false, "explanation": "not done"},'
        '{"criterion_id": "b", "pass": false, "explanation": "not done"}'
        '], "overall_score": 1.0, "summary": "All good"}'
    )
    client = MockClient(json_resp)
    judge = LiteLLMJudge(client=client)

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=checklist,
    )

    assert result["overall_score"] == 0.0
    assert result["judge_overall_score"] == 1.0
    assert result["attempts"] == 1
    assert result["raw"] == json_resp


_DUPLICATE_CHECKLIST = [
    {"id": "a", "question": "Q1?", "weight": 1.0},
    {"id": "b", "question": "Q2?", "weight": 1.0},
]


def test_score_runtime_missing_criterion_scores_half_not_a_zero():
    """``a`` true, ``b`` missing → 0.5: the missing item contributes nothing."""
    json_resp = '{"criteria": [{"criterion_id": "a", "pass": true, "explanation": "done"}]}'
    judge = LiteLLMJudge(client=MockClient(json_resp))

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=_DUPLICATE_CHECKLIST,
    )

    assert result["overall_score"] == pytest.approx(0.5)


def test_score_runtime_duplicate_criterion_id_does_not_inflate_the_score():
    """``a`` reported twice must never credit ``b``: the score stays 0.5."""
    json_resp = (
        '{"criteria": ['
        '{"criterion_id": "a", "pass": true, "explanation": "done"},'
        '{"criterion_id": "a", "pass": true, "explanation": "done again"}'
        ']}'
    )
    judge = LiteLLMJudge(client=MockClient(json_resp))

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=_DUPLICATE_CHECKLIST,
    )

    assert result["overall_score"] == pytest.approx(0.5)


def test_score_runtime_extra_duplicate_criterion_does_not_override_a_failure():
    """``a`` true, ``b`` false, extra ``a`` true → 0.5, never 1.0."""
    json_resp = (
        '{"criteria": ['
        '{"criterion_id": "a", "pass": true, "explanation": "done"},'
        '{"criterion_id": "b", "pass": false, "explanation": "missing"},'
        '{"criterion_id": "a", "pass": true, "explanation": "done again"}'
        ']}'
    )
    judge = LiteLLMJudge(client=MockClient(json_resp))

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=_DUPLICATE_CHECKLIST,
    )

    assert result["overall_score"] == pytest.approx(0.5)


def test_score_runtime_non_dict_criterion_in_scored_position_is_retried():
    """A criterion entry that is not an object in a scored position is retried."""
    checklist = [{"id": "a", "question": "Q?", "weight": 1.0}]
    json_resp = '{"criteria": ["not an object"], "overall_score": 1.0, "summary": "weird"}'
    client = ScriptedClient(json_resp)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_runtime(
            task_description="Task",
            artifact_summary="diff",
            checklist_items=checklist,
        )

    assert exc_info.value.kind == "judge_invalid_json"
    assert exc_info.value.attempts == 3
    assert client.calls == 3


def test_score_runtime_missing_criteria_is_retried():
    """A verdict without a criteria list is retried, then raised."""
    client = ScriptedClient('{"summary": "no criteria at all", "overall_score": 1.0}')
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_runtime(
            task_description="Task",
            artifact_summary="diff",
            checklist_items=[{"id": "a", "question": "Q?", "weight": 1.0}],
        )

    assert exc_info.value.kind == "judge_missing_criteria"
    assert exc_info.value.attempts == 3
    assert client.calls == 3


def test_score_runtime_retries_then_succeeds():
    """An unusable attempt is retried; the first usable verdict wins."""
    checklist = [{"id": "a", "question": "Q?", "weight": 1.0}]
    good = '{"criteria": [{"criterion_id": "a", "pass": true, "explanation": "ok"}], "overall_score": 1.0}'
    client = ScriptedClient("[Error: 429 rate limited]", good)
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    result = judge.score_runtime(
        task_description="Task",
        artifact_summary="diff",
        checklist_items=checklist,
    )

    assert client.calls == 2
    assert result["attempts"] == 2
    assert result["overall_score"] == 1.0
    assert "error_kind" not in result


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


def test_score_runtime_api_error_raises_judge_unavailable():
    """Client API errors (rate limits, model not found) are retried, then JudgeError.

    An unreachable judge is never scored as 0.0: after ``max_attempts`` tries
    the judge reports a ``judge_unavailable`` error instead.
    """
    checklist = [{"id": "x", "question": "Q?", "weight": 1.0}]
    client = ScriptedClient("[Error: HTTP 429]")
    judge = LiteLLMJudge(client=client, sleeper=lambda _: None)

    with pytest.raises(JudgeError) as exc_info:
        judge.score_runtime(
            task_description="Task",
            artifact_summary="diff",
            checklist_items=checklist,
        )

    assert exc_info.value.kind == "judge_unavailable"
    assert exc_info.value.attempts == 3
    assert client.calls == 3


def test_score_code_generation_accepts_criterion_alias():
    """A judge that returns ``criterion`` instead of ``criteria`` is still usable."""
    response = (
        '{"criterion":[{"criterion":"c1","pass":true,"explanation":"ok"}],'
        '"overall_score":1.0,"summary":"ok"}'
    )
    judge = LiteLLMJudge(client=MockClient(response))

    result = judge.score_code_generation(
        prompt="p",
        generated_code="code",
        checklist=["c1"],
        model="mock",
    )

    assert len(result["criteria"]) == 1
    assert result["overall_score"] == 1.0
    assert result["summary"] == "ok"


def test_score_bug_fixing_accepts_criterion_alias():
    """The same alias applies to bug-fix judging."""
    response = (
        '{"criterion":[{"criterion":"c1","pass":false,"explanation":"missing"}],'
        '"overall_score":0.0,"summary":"bad"}'
    )
    judge = LiteLLMJudge(client=MockClient(response))

    result = judge.score_bug_fixing(
        bug_description="b",
        proposed_fix="f",
        checklist=["c1"],
        model="mock",
    )

    assert len(result["criteria"]) == 1
    assert result["overall_score"] == 0.0
