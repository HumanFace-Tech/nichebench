"""Quiz metric that uses a Judge adapter to score multiple-choice questions."""

from __future__ import annotations

from typing import Any


class QuizMetric:
    def __init__(self, judge):
        self.judge = judge

    def score(
        self,
        *,
        task_id: str,
        question: str,
        choices: list[str],
        gold: str,
        candidate: str,
        judge_model: str = "openai/gpt-5",
        judge_notes: str | None = None,
    ) -> dict[str, Any]:
        """Return a normalized score dict for a single quiz item."""
        res = self.judge.score_quiz(
            question=question,
            choices=choices,
            gold=gold,
            candidate=candidate,
            model=judge_model,
            judge_notes=judge_notes,
        )
        # Normalize keys and ensure presence
        out = {
            "id": task_id,
            "pass": bool(res.get("pass", False)),
            "selected": res.get("selected", ""),
            "score": int(res.get("score", 1 if res.get("pass", False) else 0)),
            "explanation": res.get("explanation", ""),
            "judge_raw": res.get("raw", ""),
        }
        return out
