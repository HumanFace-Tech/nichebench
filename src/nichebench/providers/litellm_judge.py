from __future__ import annotations

from typing import Any, Optional

from .litellm_client import LiteLLMClient, parse_json_safe


class LiteLLMJudge:
    """Judge adapter that prompts a judge LLM and parses JSON output.

    The judge is authoritative: it must return a structured JSON verdict. We
    accept an optional `system_prompt` blob which is prepended to the judge
    prompt (this lets framework packs provide tailored judge instructions).
    """

    def __init__(self, client: LiteLLMClient):
        self.client = client

    def score_quiz(
        self,
        *,
        question: str,
        choices: list[str],
        gold: str,
        candidate: str,
        model: str = "openai/gpt-5",
        model_params: dict | None = None,
        system_prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        """Score a multiple-choice quiz item using an LLM judge.

        Returns a dict with keys: pass (bool), selected (str), score (int), explanation (str), raw (str)
        """
        # Build a compact prompt. Real prompts should live in prompts/JUDGE_QUIZ.py
        choices_text = "\n".join([f"{chr(65+i)}. {c}" for i, c in enumerate(choices)]) if choices else ""

        prompt_parts = []
        if system_prompt:
            prompt_parts.append(system_prompt.strip())
        prompt_parts.append("You are an evaluation judge.")
        prompt_parts.append(f"Question: {question}")
        if choices_text:
            prompt_parts.append(f"Choices:\n{choices_text}")
        prompt_parts.append(f"Gold: {gold}")
        prompt_parts.append(f"Model answer: {candidate}")
        prompt_parts.append(
            "Return a JSON object with keys: pass (true/false), selected (letter), score (0 or 1), explanation (short)."
        )

        prompt = "\n\n".join(prompt_parts)
        resp = self.client.generate(prompt=prompt, model=model, model_params=model_params)
        raw = resp.get("output", "")
        parsed = parse_json_safe(raw)
        if isinstance(parsed, dict):
            out = parsed
        else:
            # Conservative fallback: judge didn't return structured JSON.
            # We do NOT attempt to heuristically extract answers here — the
            # Judge LLM must return a proper JSON verdict. Treat as failure
            # but surface the raw judge text for debugging.
            out = {
                "pass": False,
                "selected": "",
                "score": 0,
                "explanation": "Judge did not return structured JSON. See raw output.",
            }
        out["raw"] = raw
        return out

    # No local extraction: judge must return structured JSON verdicts.
