from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from nichebench.core.prompt_loader import load_prompt_mapping

from .litellm_client import LiteLLMClient, parse_json_safe

_PROMPTS = load_prompt_mapping(Path(__file__).resolve().parent / "prompts" / "litellm_judge.yaml")


def _compute_weighted_score(
    criteria: list[dict[str, Any]],
    checklist_items: list[dict[str, Any]],
) -> float:
    """Compute weighted score from judge criteria, matched by criterion_id.

    Falls back to positional matching if fewer than half of criteria IDs are
    recognised. Weights are normalised to sum to 1.0 before scoring.
    """
    weight_by_id = {str(item.get("id", "")): float(item.get("weight", 1.0)) for item in checklist_items}
    total_weight = sum(weight_by_id.values())
    if total_weight <= 0 or not criteria:
        return 0.0

    # Try ID-based matching first
    matched_ids: set[str] = set()
    score = 0.0
    for c in criteria:
        cid = str(c.get("criterion_id", ""))
        if cid in weight_by_id:
            matched_ids.add(cid)
            pass_val = c.get("pass", False)
            if pass_val is True:
                score += weight_by_id[cid]
            elif pass_val == "partial":
                score += weight_by_id[cid] * 0.5

    # If we matched at least half the items by ID, trust the ID-based result
    if len(matched_ids) >= len(checklist_items) / 2:
        return min(1.0, score / total_weight)

    # Positional fallback: assume criteria are in the same order as checklist_items
    score = 0.0
    for i, c in enumerate(criteria):
        if i >= len(checklist_items):
            break
        weight = float(checklist_items[i].get("weight", 1.0))
        pass_val = c.get("pass", False)
        if pass_val is True:
            score += weight
        elif pass_val == "partial":
            score += weight * 0.5
    return min(1.0, score / total_weight)


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
        prompt_parts.append(str(_PROMPTS.get("quiz_default_role", "You are an evaluation judge.")))
        prompt_parts.append(f"Question: {question}")
        if choices_text:
            prompt_parts.append(f"Choices:\n{choices_text}")
        prompt_parts.append(f"Gold (correct answer): {gold}")
        prompt_parts.append(f"Model answer: {candidate}")

        # Add judge notes if provided
        if judge_notes:
            prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
            prompt_parts.append(str(_PROMPTS.get("quiz_notes_hint", "")))

        prompt_parts.append(str(_PROMPTS.get("quiz_json_contract", "")))

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
            prompt_parts.append(str(_PROMPTS.get("code_default_role", "You are an expert code reviewer.")))

        prompt_parts.append(str(_PROMPTS.get("code_eval_intro", "Evaluate the following code implementation:")))
        prompt_parts.append(f"Task/Prompt: {prompt}")
        prompt_parts.append(f"Generated Code:\n{generated_code}")
        prompt_parts.append(f"Checklist Criteria:\n{checklist_text}")

        # Add judge notes if provided
        if judge_notes:
            prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
            prompt_parts.append(str(_PROMPTS.get("code_notes_hint", "")))

        prompt_parts.append(str(_PROMPTS.get("code_json_contract", "")))

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
            prompt_parts.append(
                str(_PROMPTS.get("bug_default_role", "You are an expert code reviewer evaluating bug fixes."))
            )

        prompt_parts.append(str(_PROMPTS.get("bug_eval_intro", "Evaluate the following bug fix:")))
        prompt_parts.append(f"Bug Description: {bug_description}")
        prompt_parts.append(f"Proposed Fix:\n{proposed_fix}")
        prompt_parts.append(f"Checklist Criteria:\n{checklist_text}")

        # Add judge notes if provided
        if judge_notes:
            prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
            prompt_parts.append(str(_PROMPTS.get("bug_notes_hint", "")))

        prompt_parts.append(str(_PROMPTS.get("bug_json_contract", "")))

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

    def score_runtime(
        self,
        *,
        task_description: str,
        artifact_summary: str,
        checklist_items: list[dict[str, Any]],
        model: str = "openai/gpt-4o",
        model_params: dict | None = None,
        system_prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        """Score a runtime agentic task using an LLM judge with weighted checklist criteria.

        checklist_items: list of dicts with keys: id (str), question (str), weight (float).
        Optional keys: guidance (str), bonus (bool).

        Returns a dict with: criteria (list), overall_score (float 0.0-1.0), summary (str), raw (str)
        """
        if not checklist_items:
            return {
                "criteria": [],
                "overall_score": 1.0,
                "summary": "No checklist items to evaluate — defaulting to 1.0.",
                "raw": "",
            }

        # Build the checklist block for the prompt
        checklist_parts: list[str] = []
        for item in checklist_items:
            item_id = str(item.get("id", "unknown"))
            question = str(item.get("question", "")).strip()
            weight = float(item.get("weight", 1.0))
            guidance = str(item.get("guidance", "")).strip()
            bonus = bool(item.get("bonus", False))

            entry = f"criterion_id: {item_id}\nQuestion: {question}\nWeight: {weight}"
            if bonus:
                entry += "\n(BONUS — do not penalise if absent)"
            if guidance:
                entry += f"\nGuidance: {guidance}"
            checklist_parts.append(entry)

        checklist_text = "\n\n---\n\n".join(checklist_parts)

        prompt_parts: list[str] = []
        if system_prompt:
            prompt_parts.append(system_prompt.strip())
        else:
            prompt_parts.append(
                str(
                    _PROMPTS.get(
                        "runtime_default_role",
                        "You are an expert evaluating an AI agent's Drupal engineering work.",
                    )
                )
            )

        prompt_parts.append("## Task Description\n\n" + task_description.strip())
        prompt_parts.append("## Agent Artifacts\n\n" + artifact_summary)
        prompt_parts.append(str(_PROMPTS.get("runtime_checklist_intro", "")) + "\n\n" + checklist_text)
        prompt_parts.append(str(_PROMPTS.get("runtime_json_contract", "")))

        full_prompt = "\n\n".join(prompt_parts)
        resp = self.client.generate(prompt=full_prompt, model=model, model_params=model_params)
        raw = resp.get("output", "")

        # LiteLLMClient.generate() never raises — it returns "[Error: ...]" on API/network failure.
        # Re-raise so the executor's outer except-block leaves judge_score=None and falls back to
        # deterministic-only scoring instead of treating an unreachable model as a zero score.
        if raw.startswith("[Error:"):
            raise RuntimeError(raw)

        parsed = parse_json_safe(raw)

        if isinstance(parsed, dict) and "criteria" in parsed:
            out = parsed
            # Recompute overall_score from weighted criteria if judge didn't return a valid float
            score_val = out.get("overall_score")
            if not isinstance(score_val, (int, float)):
                out["overall_score"] = _compute_weighted_score(out.get("criteria", []), checklist_items)
            else:
                # Clamp to [0.0, 1.0]
                out["overall_score"] = max(0.0, min(1.0, float(score_val)))
        else:
            # Conservative fallback: judge returned invalid JSON — score 0.0, no randomness
            out = {
                "criteria": [
                    {
                        "criterion_id": str(item.get("id", "")),
                        "pass": False,
                        "explanation": "Judge returned invalid JSON",
                    }
                    for item in checklist_items
                ],
                "overall_score": 0.0,
                "summary": "Judge did not return structured JSON. See raw output.",
            }

        out["raw"] = raw
        return out

    # No local extraction: judge must return structured JSON verdicts.
