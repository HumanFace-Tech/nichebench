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
        judge_notes: Optional[str] = None,
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
        prompt_parts.append(f"Gold (correct answer): {gold}")
        prompt_parts.append(f"Model answer: {candidate}")

        # Add judge notes if provided
        if judge_notes:
            prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
            prompt_parts.append(
                "Note: Use the above context to better understand the question and make more accurate assessments."
            )

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

    def score_code_generation(
        self,
        *,
        prompt: str,
        generated_code: str,
        checklist: list[str],
        model: str = "openai/gpt-4o",
        model_params: dict | None = None,
        system_prompt: Optional[str] = None,
        judge_notes: Optional[str] = None,
    ) -> dict[str, Any]:
        """Score a code generation task using an LLM judge with checklist criteria.

        Returns a dict with keys: criteria (list), overall_score (float), summary (str), raw (str)
        """
        # Check if this is an error case - skip evaluation
        if generated_code.startswith("[ERROR:") and (
            "repetitive content" in generated_code or "Model misbehavior" in generated_code
        ):
            return {
                "criteria": [],
                "overall_score": 0.0,
                "summary": "Evaluation skipped due to model repetitive behavior",
                "raw": f"Skipped evaluation for repetitive content: {generated_code[:100]}...",
            }

        # Build the evaluation prompt
        checklist_text = (
            "\n".join([f"- {item}" for item in checklist]) if checklist else "No specific criteria provided"
        )

        prompt_parts = []
        if system_prompt:
            prompt_parts.append(system_prompt.strip())
        else:
            prompt_parts.append("You are an expert code reviewer.")

        prompt_parts.append("Evaluate the following code implementation:")
        prompt_parts.append(f"Task/Prompt: {prompt}")
        prompt_parts.append(f"Generated Code:\n{generated_code}")
        prompt_parts.append(f"Checklist Criteria:\n{checklist_text}")

        # Add judge notes if provided
        if judge_notes:
            prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
            prompt_parts.append(
                "Note: Use the above context to better understand the requirements and make more accurate assessments."
            )

        prompt_parts.append(
            "Return a JSON object with: criteria (stick to the checklist!) (array of {criterion, pass, explanation}), "
            "overall_score (0.0-1.0), summary (brief assessment)."
        )

        full_prompt = "\n\n".join(prompt_parts)

        resp = self.client.generate(prompt=full_prompt, model=model, model_params=model_params)

        raw = resp.get("output", "")
        parsed = parse_json_safe(raw)

        if isinstance(parsed, dict) and "criteria" in parsed:
            out = parsed
            # Ensure overall_score is present and valid
            if "overall_score" not in out:
                # Calculate score from criteria if not provided (code generation)
                if "criteria" in out and out["criteria"]:
                    total_score = 0.0
                    total_items = len(out["criteria"])
                    for c in out["criteria"]:
                        pass_value = c.get("pass", False)
                        if pass_value is True:
                            total_score += 1.0
                        elif pass_value == "partial":
                            total_score += 0.5
                        # False adds 0.0
                    out["overall_score"] = total_score / total_items if total_items > 0 else 0.0
                else:
                    out["overall_score"] = 0.0
        else:
            # Conservative fallback: judge didn't return structured JSON
            out = {
                "criteria": [
                    {"criterion": item, "pass": False, "explanation": "Judge returned invalid JSON"}
                    for item in checklist
                ],
                "overall_score": 0.0,
                "summary": "Judge did not return structured JSON. See raw output.",
            }

        out["raw"] = raw
        return out

    def score_bug_fixing(
        self,
        *,
        bug_description: str,
        proposed_fix: str,
        checklist: list[str],
        model: str = "openai/gpt-4o",
        model_params: dict | None = None,
        system_prompt: Optional[str] = None,
        judge_notes: Optional[str] = None,
    ) -> dict[str, Any]:
        """Score a bug fixing task using an LLM judge with checklist criteria.

        Returns a dict with keys: criteria (list), overall_score (float), summary (str), raw (str)
        """
        # Build the evaluation prompt
        checklist_text = (
            "\n".join([f"- {item}" for item in checklist]) if checklist else "No specific criteria provided"
        )

        prompt_parts = []
        if system_prompt:
            prompt_parts.append(system_prompt.strip())
        else:
            prompt_parts.append("You are an expert code reviewer evaluating bug fixes.")

        prompt_parts.append("Evaluate the following bug fix:")
        prompt_parts.append(f"Bug Description: {bug_description}")
        prompt_parts.append(f"Proposed Fix:\n{proposed_fix}")
        prompt_parts.append(f"Checklist Criteria:\n{checklist_text}")

        # Add judge notes if provided
        if judge_notes:
            prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
            prompt_parts.append(
                "Note: Use the above context to better understand the requirements and make more accurate assessments."
            )

        prompt_parts.append(
            "Return a JSON object with: criteria (array of {criterion, pass, explanation}), "
            "overall_score (0.0-1.0), summary (brief assessment)."
        )

        full_prompt = "\n\n".join(prompt_parts)
        resp = self.client.generate(prompt=full_prompt, model=model, model_params=model_params)
        raw = resp.get("output", "")
        parsed = parse_json_safe(raw)

        if isinstance(parsed, dict) and "criteria" in parsed:
            out = parsed
            # Ensure overall_score is present and valid
            if "overall_score" not in out:
                # Calculate score from criteria if not provided (bug fixing)
                if "criteria" in out and out["criteria"]:
                    total_score = 0.0
                    total_items = len(out["criteria"])
                    for c in out["criteria"]:
                        pass_value = c.get("pass", False)
                        if pass_value is True:
                            total_score += 1.0
                        elif pass_value == "partial":
                            total_score += 0.5
                        # False adds 0.0
                    out["overall_score"] = total_score / total_items if total_items > 0 else 0.0
                else:
                    out["overall_score"] = 0.0
        else:
            # Conservative fallback: judge didn't return structured JSON
            out = {
                "criteria": [
                    {"criterion": item, "pass": False, "explanation": "Judge returned invalid JSON"}
                    for item in checklist
                ],
                "overall_score": 0.0,
                "summary": "Judge did not return structured JSON. See raw output.",
            }

        out["raw"] = raw
        return out

    # No local extraction: judge must return structured JSON verdicts.
