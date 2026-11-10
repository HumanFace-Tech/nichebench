"""Retry and error semantics for LiteLLMJudge code/bug rubric scoring.

Covers the code-generation v2 C2 slice:

* Unusable judge output (``[Error:]``, empty, non-JSON, missing
  ``criteria``, unscorable rubric) is retried up to ``max_attempts``
  times with exponential backoff through the injected ``sleeper``.
* Exhaustion raises ``JudgeError`` with the error ``kind`` and attempt
  count — never a fabricated zero score.
* A usable rubric is never retried, no matter how low (or out-of-range)
  its ``overall_score`` — the score is recomputed from the criteria and
  the judge's own number is kept under ``judge_overall_score``.
"""

from __future__ import annotations

import json
from typing import Optional

import pytest

from nichebench.providers.litellm_judge import JudgeError, LiteLLMJudge

CHECKLIST = ["criterion one", "criterion two"]


def _verdict(passes: tuple, overall: Optional[float] = None, summary: str = "ok") -> str:
    payload = {
        "criteria": [
            {"criterion": f"c{i}", "pass": value, "explanation": "why"} for i, value in enumerate(passes)
        ],
        "summary": summary,
    }
    if overall is not None:
        payload["overall_score"] = overall
    return json.dumps(payload)


class _SeqClient:
    """``generate()`` that replays a fixed sequence (last output repeats)."""

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.calls: list[str] = []

    def generate(self, prompt: str, model: str, model_params=None) -> dict:
        index = min(len(self.calls), len(self.outputs) - 1)
        self.calls.append(prompt)
        return {"model": model, "output": self.outputs[index]}


def _make_judge(
    *outputs: str,
    retry_delay: float = 0.0,
    delays: Optional[list] = None,
    max_attempts: int = 3,
) -> LiteLLMJudge:
    """Build a judge with a scripted client and an injected sleeper."""
    record = delays if delays is not None else []
    return LiteLLMJudge(
        client=_SeqClient(*outputs),
        max_attempts=max_attempts,
        retry_delay=retry_delay,
        sleeper=record.append,
    )


def _score(judge: LiteLLMJudge, checklist: list[str] | None = None) -> dict:
    return judge.score_code_generation(
        prompt="the prompt",
        generated_code="the code",
        checklist=CHECKLIST if checklist is None else checklist,
        model="mock/judge",
    )


class TestRetryOnUnusableOutput:
    def test_error_then_valid_retries_once(self):
        verdict = _verdict((True, False), overall=0.95)
        judge = _make_judge("[Error: provider down]", verdict)

        result = _score(judge)

        assert len(judge.client.calls) == 2
        assert result["attempts"] == 2
        assert result["overall_score"] == pytest.approx(0.5)
        assert result["raw"] == verdict
        # The judge's own inflated number is kept for audit only.
        assert result["judge_overall_score"] == pytest.approx(0.95)

    def test_repeated_errors_exhaust_retries(self):
        judge = _make_judge("[Error: a]", "[Error: b]", "[Error: c]")

        with pytest.raises(JudgeError) as excinfo:
            _score(judge)

        exc = excinfo.value
        assert exc.kind == "judge_unavailable"
        assert exc.attempts == 3
        assert exc.last_raw == "[Error: c]"
        assert str(exc) == "[Error: c]"
        assert len(judge.client.calls) == 3

    def test_invalid_json_then_valid(self):
        judge = _make_judge("certainly not json", _verdict((True, True)))

        result = _score(judge)

        assert len(judge.client.calls) == 2
        assert result["attempts"] == 2
        assert result["overall_score"] == pytest.approx(1.0)

    def test_missing_criteria_then_valid(self):
        no_criteria = json.dumps({"overall_score": 0.9, "summary": "no criteria list"})
        judge = _make_judge(no_criteria, _verdict((True, False)))

        result = _score(judge)

        assert len(judge.client.calls) == 2
        assert result["attempts"] == 2
        assert result["overall_score"] == pytest.approx(0.5)

    def test_empty_output_then_valid(self):
        judge = _make_judge("", _verdict((True, False)))

        result = _score(judge)

        assert len(judge.client.calls) == 2
        assert result["attempts"] == 2

    def test_unscorable_rubric_exhausts_retries(self):
        # An empty checklist makes criteria_to_score return None every time.
        judge = _make_judge(_verdict((True,), overall=1.0))

        with pytest.raises(JudgeError) as excinfo:
            _score(judge, checklist=[])

        assert excinfo.value.kind == "judge_missing_criteria"
        assert excinfo.value.attempts == 3
        assert len(judge.client.calls) == 3

    def test_bug_fixing_shares_the_retry_path(self):
        judge = _make_judge("[Error: down]", _verdict((True, False)))

        result = judge.score_bug_fixing(
            bug_description="the bug",
            proposed_fix="the fix",
            checklist=CHECKLIST,
            model="mock/judge",
        )

        assert len(judge.client.calls) == 2
        assert result["attempts"] == 2
        assert result["overall_score"] == pytest.approx(0.5)


class TestNoRetryOnUsableVerdicts:
    def test_valid_low_score_is_not_retried(self):
        delays: list[float] = []
        judge = _make_judge(_verdict((False, False), overall=0.0), delays=delays)

        result = _score(judge)

        assert len(judge.client.calls) == 1
        assert result["attempts"] == 1
        assert result["overall_score"] == pytest.approx(0.0)
        assert delays == []

    def test_out_of_range_overall_score_is_recomputed_without_retry(self):
        # Judge claims 1.2 with every criterion failed → result stays 0.0.
        judge = _make_judge(_verdict((False, False), overall=1.2))

        result = _score(judge)

        assert len(judge.client.calls) == 1
        assert result["attempts"] == 1
        assert result["overall_score"] == pytest.approx(0.0)
        assert result["judge_overall_score"] == pytest.approx(1.2)

    def test_partial_verdict_scores_without_retry(self):
        judge = _make_judge(_verdict((True, "partial"), overall=0.7))

        result = _score(judge)

        assert len(judge.client.calls) == 1
        assert result["overall_score"] == pytest.approx(0.75)
        assert result["judge_overall_score"] == pytest.approx(0.7)


class TestBackoff:
    def test_delays_double_between_attempts(self):
        delays: list[float] = []
        judge = _make_judge(
            "[Error: a]",
            "[Error: b]",
            _verdict((True, True)),
            retry_delay=1.0,
            delays=delays,
        )

        result = _score(judge)

        assert len(judge.client.calls) == 3
        assert result["attempts"] == 3
        # 1s before attempt 2, 2s before attempt 3 — no sleep after success.
        assert delays == [1.0, 2.0]

    def test_retry_delay_zero_keeps_tests_instant(self):
        delays: list[float] = []
        judge = _make_judge("[Error: a]", _verdict((True,)), retry_delay=0.0, delays=delays)

        result = _score(judge)

        assert result["attempts"] == 2
        assert delays == [0.0]
