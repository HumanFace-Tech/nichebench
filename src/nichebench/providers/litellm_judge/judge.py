"""LiteLLMJudge — judge adapter that prompts a judge LLM and parses JSON output.

This module is the *port* between the benchmark framework and the judge LLM.
All judge LLM calls are issued through :class:`LiteLLMJudge`; the only
consumer is :mod:`nichebench.execution.runners.judge`.

Ownership
=========
This module is owned by the ``litellm_judge`` package. It depends on:

* ``scoring.py`` — :func:`_compute_weighted_score` for weighted runtime scoring
* ``parsing.py`` — :func:`parse_json_safe` for JSON extraction
* ``categories.py`` — ``build_*_prompt`` helpers for each task category
* ``litellm_client.py`` — the underlying ``LiteLLMClient`` wrapper

Public API
==========
The class is re-exported from the package ``__init__.py``. External callers
must not import from sub-modules directly.

Judge prompt contract
=====================
Every ``score_*`` method composes a prompt following the documented
*prompt contract* stored in
``nichebench/providers/prompts/litellm_judge.yaml`` (loaded once at import).
The contract specifies:

* the default system role for each task category
* the structure of the user prompt (task, artifact, checklist)
* the required JSON output schema (``json_contract``)

The judge **must** return a valid JSON object conforming to the contract.
If it returns non-JSON or malformed data the method falls back to a
zero-scoring verdict with no randomness introduced.

Failure handling
================
* ``LiteLLMClient.generate()`` never raises — network/API failures return
  ``"[Error: ...]"`` strings. :meth:`score_runtime` re-raises these as
  ``RuntimeError`` so the executor's outer retry logic can handle them
  and fall back to deterministic-only scoring.
* All other ``score_*`` methods return a zero-score verdict on any error
  path rather than raising, because they are called inside DeepEval metric
  wrappers that manage their own retry budgets.
"""

from typing import Any, Optional

from nichebench.providers.litellm_client import LiteLLMClient

from .categories import (
    build_bug_fixing_prompt,
    build_code_generation_prompt,
    build_quiz_prompt,
    build_runtime_prompt,
)
from .parsing import parse_json_safe
from .scoring import _compute_weighted_score


class LiteLLMJudge:
    """Judge adapter that prompts a judge LLM and parses JSON output.

    The judge is authoritative: it must return a structured JSON verdict. We
    accept an optional ``system_prompt`` blob which is prepended to the judge
    prompt (this lets framework packs provide tailored judge instructions).
    """

    def __init__(self, client: LiteLLMClient):
        """Initialize with a shared LiteLLMClient instance.

        Args:
            client: the shared LiteLLMClient used for both judge and MUT calls.
        """
        self.client = client

    # -------------------------------------------------------------------------
    # Quiz
    # -------------------------------------------------------------------------

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
        """Score a single multiple-choice quiz item.

        Returns:
            dict with keys: ``pass`` (bool), ``selected`` (str),
            ``score`` (int 0–1), ``explanation`` (str), ``raw`` (str, the
            unmodified judge output for debugging).
        """
        prompt = build_quiz_prompt(
            question=question,
            choices=choices,
            gold=gold,
            candidate=candidate,
            system_prompt=system_prompt,
            judge_notes=judge_notes,
        )

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

    # -------------------------------------------------------------------------
    # Code generation
    # -------------------------------------------------------------------------

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
        """Score a code generation task against a checklist of criteria.

        Error-type model outputs (``[ERROR: repetitive content ...]``) are
        detected early and return a zero score without calling the judge.

        Returns:
            dict with keys: ``criteria`` (list of per-item dicts),
            ``overall_score`` (float 0.0–1.0), ``summary`` (str),
            ``raw`` (str, raw judge output).
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

        prompt_text = build_code_generation_prompt(
            prompt=prompt,
            generated_code=generated_code,
            checklist=checklist,
            system_prompt=system_prompt,
            judge_notes=judge_notes,
        )

        resp = self.client.generate(prompt=prompt_text, model=model, model_params=model_params)
        raw = resp.get("output", "")
        parsed = parse_json_safe(raw)

        if isinstance(parsed, dict) and "criteria" in parsed and isinstance(parsed["criteria"], list):
            out = parsed
            # Ensure overall_score is present and valid
            if "overall_score" not in out:
                if "criteria" in out and out["criteria"]:
                    total_score = 0.0
                    total_items = len(out["criteria"])
                    for c in out["criteria"]:
                        if not isinstance(c, dict):
                            continue
                        pass_value = c.get("pass", False)
                        if pass_value is True:
                            total_score += 1.0
                        elif pass_value == "partial":
                            total_score += 0.5
                    out["overall_score"] = total_score / total_items if total_items > 0 else 0.0
                else:
                    out["overall_score"] = 0.0
        else:
            # Conservative fallback: judge didn't return structured JSON or
            # ``criteria`` was not a list of dicts (e.g., a string or
            # missing).  We do not attempt to heuristically extract values
            # — fall back to a zero score.
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

    # -------------------------------------------------------------------------
    # Bug fixing
    # -------------------------------------------------------------------------

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
        """Score a bug fixing task against a checklist of criteria.

        Mirrors the structure of :meth:`score_code_generation` but uses
        the bug-fix prompt contract.

        Returns:
            dict with keys: ``criteria``, ``overall_score``, ``summary``,
            ``raw``.
        """
        prompt_text = build_bug_fixing_prompt(
            bug_description=bug_description,
            proposed_fix=proposed_fix,
            checklist=checklist,
            system_prompt=system_prompt,
            judge_notes=judge_notes,
        )

        resp = self.client.generate(prompt=prompt_text, model=model, model_params=model_params)
        raw = resp.get("output", "")
        parsed = parse_json_safe(raw)

        if isinstance(parsed, dict) and "criteria" in parsed and isinstance(parsed["criteria"], list):
            out = parsed
            if "overall_score" not in out:
                if "criteria" in out and out["criteria"]:
                    total_score = 0.0
                    total_items = len(out["criteria"])
                    for c in out["criteria"]:
                        if not isinstance(c, dict):
                            continue
                        pass_value = c.get("pass", False)
                        if pass_value is True:
                            total_score += 1.0
                        elif pass_value == "partial":
                            total_score += 0.5
                    out["overall_score"] = total_score / total_items if total_items > 0 else 0.0
                else:
                    out["overall_score"] = 0.0
        else:
            # Conservative fallback: judge didn't return structured JSON or
            # ``criteria`` was not a list of dicts.
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

    # -------------------------------------------------------------------------
    # Runtime (drupal_runtime tasks)
    # -------------------------------------------------------------------------

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
        """Score a runtime (agentic) task against a weighted checklist.

        This is the method used for ``drupal_runtime`` tasks. It receives
        a pre-assembled artifact summary (diff, log, checks, PHPCS, PHPStan)
        and a manifest-defined checklist with per-item ``weight``,
        ``question``, ``guidance``, and ``bonus`` fields.

        Scoring: the judge returns per-criterion ``pass`` values.
        ``overall_score`` is computed by :func:`_compute_weighted_score`
        which matches criteria by ``criterion_id`` (primary) or position
        (fallback). ``overall_score`` is clamped to ``[0.0, 1.0]``.

        **Error propagation**: if the judge is unreachable (``[Error:]``)
        this method raises ``RuntimeError`` so the executor can fall back
        to deterministic-only scoring rather than silently awarding zero.

        Returns:
            dict with keys: ``criteria`` (list), ``overall_score`` (float),
            ``summary`` (str), ``raw`` (str).
        """
        if not checklist_items:
            return {
                "criteria": [],
                "overall_score": 1.0,
                "summary": "No checklist items to evaluate — defaulting to 1.0.",
                "raw": "",
            }

        prompt_text = build_runtime_prompt(
            task_description=task_description,
            artifact_summary=artifact_summary,
            checklist_items=checklist_items,
            system_prompt=system_prompt,
        )

        resp = self.client.generate(prompt=prompt_text, model=model, model_params=model_params)
        raw = resp.get("output", "")

        # LiteLLMClient.generate() never raises — it returns "[Error: ...]" on API/network failure.
        # Re-raise so the executor's outer except-block leaves judge_score=None and falls back to
        # deterministic-only scoring instead of treating an unreachable model as a zero score.
        if raw.startswith("[Error:"):
            raise RuntimeError(raw)

        parsed = parse_json_safe(raw)

        if isinstance(parsed, dict) and "criteria" in parsed and isinstance(parsed["criteria"], list):
            out = parsed
            # Recompute overall_score from weighted criteria if judge didn't return a valid float
            score_val = out.get("overall_score")
            if not isinstance(score_val, (int, float)) or isinstance(score_val, bool):
                out["overall_score"] = _compute_weighted_score(out.get("criteria", []), checklist_items)
            else:
                # Clamp to [0.0, 1.0]
                out["overall_score"] = max(0.0, min(1.0, float(score_val)))
        else:
            # Conservative fallback: judge returned invalid JSON or ``criteria``
            # is not a list of dicts.  Fall back to a zero score with
            # placeholder criteria — never raise, since runtime scoring
            # has its own outer exception handler.
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
