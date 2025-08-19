"""Adapter to convert litellm responses into a format deepeval-like runners could consume.

This is a stub used for local development and tests.
"""


class JudgeAdapter:
    def __init__(self, client):
        self.client = client

    def evaluate_checklist(self, checklist: list[str], candidate: str) -> dict:
        # Simple heuristic: mark items present in candidate as passed.
        results = {}
        lower = candidate.lower()
        for item in checklist:
            key = item
            results[key] = item.lower() in lower
        score = sum(bool(v) for v in results.values()) / max(1, len(results))
        return {"per_item": results, "score": score}

    def extract_quiz_letter(self, candidate: str) -> str | None:
        # find first capital letter A-E in candidate
        import re

        m = re.search(r"\b([A-E])\b", candidate)
        if m:
            return m.group(1)
        return None
