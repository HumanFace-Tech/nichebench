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

Failure handling
================
* ``LiteLLMClient.generate()`` never raises — network/API failures return
  ``"[Error: ...]"`` strings. :meth:`score_runtime` re-raises these as
  ``RuntimeError`` so the executor's outer retry logic can handle them
  and fall back to deterministic-only scoring.
* :meth:`score_code_generation` / :meth:`score_bug_fixing` retry unusable
  verdicts (``[Error: ...]``, empty/non-JSON output, missing ``criteria``,
  unscorable rubric) up to ``max_attempts`` times and raise
  :class:`JudgeError` when every attempt fails; they never fabricate a
  zero score for an unreachable judge.
* :meth:`score_quiz` retries unusable verdicts (``[Error: ...]``,
  empty/non-JSON output, missing/unusable ``selected``, ``selected``
  outside the offered letters) the same way and raises
  :class:`JudgeError` when every attempt fails.  A valid but wrong
  answer is never retried — the judge's ``selected`` letter is
  authoritative and is compared with ``gold`` by this adapter.
"""

import math
import time
from typing import Any, Callable, Optional

from nichebench.metrics.rubric import criteria_to_score
from nichebench.metrics.types import (
    JUDGE_ERROR_INVALID_JSON,
    JUDGE_ERROR_MISSING_CRITERIA,
    JUDGE_ERROR_MISSING_SELECTED,
    JUDGE_ERROR_OUT_OF_RANGE,
    JUDGE_ERROR_UNAVAILABLE,
)
from nichebench.providers.litellm_client import LiteLLMClient

from .categories import (
    build_bug_fixing_prompt,
    build_code_generation_prompt,
    build_quiz_prompt,
    build_runtime_prompt,
)
from .parsing import parse_json_safe
from .scoring import _compute_weighted_score


class JudgeError(Exception):
    """Raised when the judge could not produce a usable verdict.

    Attributes:
        kind: machine-readable error kind from the ``JUDGE_ERROR_*``
            taxonomy (e.g. ``judge_unavailable``, ``judge_invalid_json``,
            ``judge_missing_selected``, ``judge_out_of_range``).
        attempts: number of attempts made before giving up.
        last_raw: raw judge output of the final attempt.
        last_reason: human-readable reason the final attempt failed.
    """

    def __init__(self, kind: str, attempts: int, last_raw: str, last_reason: str) -> None:
        super().__init__(last_reason)
        self.kind = kind
        self.attempts = attempts
        self.last_raw = last_raw
        self.last_reason = last_reason


def _normalize_criteria_key(parsed: Any) -> Any:
    """Accept ``criterion`` as an alias for the ``criteria`` list.

    Some judges return ``{"criterion": [...]}`` despite the prompt contract.
    The payload is otherwise valid, so normalise the key rather than throwing
    away a usable rubric.
    """
    if isinstance(parsed, dict) and "criteria" not in parsed:
        value = parsed.get("criterion")
        if isinstance(value, list):
            normalized = dict(parsed)
            normalized["criteria"] = normalized.pop("criterion")
            return normalized
    return parsed


class LiteLLMJudge:
    """Judge adapter that prompts a judge LLM and parses JSON output.

    The judge is authoritative: it must return a structured JSON verdict. We
    accept an optional ``system_prompt`` blob which is prepended to the judge
    prompt (this lets framework packs provide tailored judge instructions).
    """

    def __init__(
        self,
        client: LiteLLMClient,
        max_attempts: int = 3,
        retry_delay: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        """Initialize with a shared LiteLLMClient instance.

        Args:
            client: the shared LiteLLMClient used for both judge and MUT calls.
            max_attempts: total judge calls per verdict for code/bug scoring.
            retry_delay: base delay in seconds between attempts; the delay
                before attempt ``n`` is ``retry_delay * 2 ** (n - 1)``.
            sleeper: sleep function used between attempts — injected in
                tests so retries never actually wait.
        """
        self.client = client
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.sleeper = sleeper

    # -------------------------------------------------------------------------
    # Shared rubric scoring (code generation / bug fixing)
    # -------------------------------------------------------------------------

    def _score_rubric(
        self,
        prompt_text: str,
        checklist: list[str],
        model: str,
        model_params: dict | None,
    ) -> dict[str, Any]:
        """Call the judge and return a verified rubric verdict.

        Shared by :meth:`score_code_generation` and :meth:`score_bug_fixing`.
        The judge gets up to ``max_attempts`` tries; an attempt is retried
        only when its output is unusable — an ``[Error: ...]`` payload,
        empty output, a non-JSON body, a missing/non-list ``criteria``
        array, or a verdict the rubric cannot score.  A usable rubric with
        a low (or out-of-range) ``overall_score`` is **not** retried: the
        score is recomputed from the criteria either way, and the judge's
        own number is preserved under ``judge_overall_score`` for audit.

        Between attempts the call sleeps ``retry_delay * 2 ** (attempt - 1)``
        seconds through the injected ``sleeper``.

        Returns:
            Parsed judge verdict with ``criteria`` set, the recomputed
            ``overall_score``, the audit-only ``judge_overall_score``,
            ``attempts`` (1-based) and ``raw``.

        Raises:
            JudgeError: when every attempt produced an unusable verdict.
        """
        last_kind = JUDGE_ERROR_UNAVAILABLE
        last_raw = ""
        last_reason = "judge did not produce a usable verdict"

        for attempt in range(1, self.max_attempts + 1):
            resp = self.client.generate(prompt=prompt_text, model=model, model_params=model_params)
            raw = resp.get("output", "")

            verdict, kind, reason = self._attempt_rubric(raw, checklist, attempt)
            if verdict is not None:
                return verdict
            last_kind, last_raw, last_reason = kind, raw, reason

            if attempt < self.max_attempts:
                self.sleeper(self.retry_delay * (2 ** (attempt - 1)))

        raise JudgeError(kind=last_kind, attempts=self.max_attempts, last_raw=last_raw, last_reason=last_reason)

    def _attempt_rubric(
        self, raw: str, checklist: list[str], attempt: int
    ) -> tuple[Optional[dict[str, Any]], str, str]:
        """Validate and score a single judge response.

        Returns ``(verdict, "", "")`` on success, or
        ``(None, error_kind, reason)`` when the response must be retried.
        """
        if raw.startswith("[Error:"):
            return None, JUDGE_ERROR_UNAVAILABLE, raw
        if not raw:
            return None, JUDGE_ERROR_INVALID_JSON, "judge returned empty output"

        parsed = _normalize_criteria_key(parse_json_safe(raw))
        if not isinstance(parsed, dict):
            return None, JUDGE_ERROR_INVALID_JSON, "judge did not return a JSON object"

        criteria = parsed.get("criteria")
        if not isinstance(criteria, list):
            return None, JUDGE_ERROR_MISSING_CRITERIA, "judge verdict has no criteria list"

        computed = criteria_to_score(criteria, checklist)
        if computed is None:
            return None, JUDGE_ERROR_MISSING_CRITERIA, "score could not be recomputed from criteria"

        # The recomputed score is authoritative; the judge's own
        # overall_score is audit-only (kept raw when it is a finite number).
        judge_score = parsed.get("overall_score")
        if (
            isinstance(judge_score, bool)
            or not isinstance(judge_score, (int, float))
            or not math.isfinite(float(judge_score))
        ):
            parsed["judge_overall_score"] = None
        else:
            parsed["judge_overall_score"] = float(judge_score)

        parsed["criteria"] = criteria
        parsed["overall_score"] = computed
        parsed["attempts"] = attempt
        parsed["raw"] = raw
        return parsed, "", ""

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
        """Score a single multiple-choice quiz item with the judge LLM.

        The judge is authoritative: it must return a JSON verdict carrying
        a usable ``selected`` letter.  The judge gets up to ``max_attempts``
        tries; an attempt is retried only when its output is unusable — an
        ``[Error: ...]`` payload, empty output, a non-JSON body, a missing
        or unusable ``selected`` field, or a ``selected`` letter outside the
        offered range.  A valid but *wrong* answer is **not** retried: a low
        score is a legitimate verdict.

        On success ``selected`` is normalised to a single offered letter and
        compared with the normalised ``gold``: ``pass``, ``score`` and
        ``overall_score`` are recomputed from that comparison (the judge's
        own ``pass`` is never trusted over its ``selected``).  The judge's
        original boolean is preserved under ``judge_pass`` for audit, with
        ``judge_pass_mismatch`` set when it disagrees with the recomputed
        verdict.

        Between attempts the call sleeps ``retry_delay * 2 ** (attempt - 1)``
        seconds through the injected ``sleeper``.

        Returns:
            dict with keys ``pass`` (bool), ``selected`` (normalised letter),
            ``score`` / ``overall_score`` (1.0 or 0.0), ``explanation`` (str),
            ``judge_pass`` (the judge's own boolean, or ``None``),
            ``judge_pass_mismatch`` (bool), ``attempts`` (1-based int) and
            ``raw`` (the unmodified judge output for debugging).

        Raises:
            JudgeError: when every attempt produced an unusable verdict —
                kind ``judge_unavailable`` (API/network),
                ``judge_invalid_json``, ``judge_missing_selected`` or
                ``judge_out_of_range`` — instead of a fabricated zero score.
        """
        prompt = build_quiz_prompt(
            question=question,
            choices=choices,
            gold=gold,
            candidate=candidate,
            system_prompt=system_prompt,
            judge_notes=judge_notes,
        )
        gold_letter = (gold or "").strip().upper()

        last_kind = JUDGE_ERROR_UNAVAILABLE
        last_raw = ""
        last_reason = "judge did not produce a usable quiz verdict"

        for attempt in range(1, self.max_attempts + 1):
            resp = self.client.generate(prompt=prompt, model=model, model_params=model_params)
            raw = resp.get("output", "")

            verdict, kind, reason = self._attempt_quiz(raw, choices)
            if verdict is None:
                last_kind, last_raw, last_reason = kind, raw, reason
                if attempt < self.max_attempts:
                    self.sleeper(self.retry_delay * (2 ** (attempt - 1)))
                continue

            selected = verdict["selected"]
            passed = selected == gold_letter
            judge_pass = verdict.get("pass")
            judge_pass_bool = judge_pass if isinstance(judge_pass, bool) else None

            explanation = verdict.get("explanation")
            verdict["pass"] = passed
            verdict["score"] = 1.0 if passed else 0.0
            verdict["overall_score"] = 1.0 if passed else 0.0
            verdict["explanation"] = (
                str(explanation)
                if isinstance(explanation, str) and explanation.strip()
                else f"Selected {selected}; gold {gold_letter}"
            )
            verdict["judge_pass"] = judge_pass_bool
            verdict["judge_pass_mismatch"] = judge_pass_bool is not None and judge_pass_bool != passed
            verdict["attempts"] = attempt
            verdict["raw"] = raw
            return verdict

        raise JudgeError(kind=last_kind, attempts=self.max_attempts, last_raw=last_raw, last_reason=last_reason)

    def _attempt_quiz(
        self, raw: str, choices: list[str]
    ) -> tuple[Optional[dict[str, Any]], str, str]:
        """Validate and normalise a single quiz judge response.

        Returns ``(verdict, "", "")`` on success with ``selected`` normalised
        to a single offered letter, or ``(None, error_kind, reason)`` when
        the response must be retried.
        """
        if raw.startswith("[Error:"):
            return None, JUDGE_ERROR_UNAVAILABLE, raw
        if not raw:
            return None, JUDGE_ERROR_INVALID_JSON, "judge returned empty output"

        parsed = parse_json_safe(raw)
        if not isinstance(parsed, dict):
            return None, JUDGE_ERROR_INVALID_JSON, "judge did not return a JSON object"

        selected_raw = parsed.get("selected")
        if not isinstance(selected_raw, str) or not selected_raw.strip():
            return None, JUDGE_ERROR_MISSING_SELECTED, "judge verdict has no usable selected letter"

        selected = selected_raw.strip().upper()
        if len(selected) != 1 or not selected.isalpha():
            return None, JUDGE_ERROR_MISSING_SELECTED, "judge selected is not a single letter"

        allowed = {chr(ord("A") + index) for index in range(len(choices))}
        if selected not in allowed:
            return None, JUDGE_ERROR_OUT_OF_RANGE, f"judge selected {selected} outside the offered choices"

        parsed["selected"] = selected
        return parsed, "", ""

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
            ``overall_score`` (float 0.0–1.0, recomputed from the
            criteria), ``judge_overall_score`` (the judge's own reported
            score, audit-only), ``summary`` (str), ``attempts`` (int),
            ``raw`` (str, raw judge output).

        Raises:
            JudgeError: when the judge never returns a usable rubric
                verdict within ``max_attempts`` tries (instead of the
                old fabricated zero score).
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

        return self._score_rubric(prompt_text, checklist, model, model_params)

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
            dict with keys: ``criteria``, ``overall_score`` (recomputed
            from the criteria), ``judge_overall_score`` (audit-only),
            ``summary``, ``attempts``, ``raw``.

        Raises:
            JudgeError: when the judge never returns a usable rubric
                verdict within ``max_attempts`` tries.
        """
        prompt_text = build_bug_fixing_prompt(
            bug_description=bug_description,
            proposed_fix=proposed_fix,
            checklist=checklist,
            system_prompt=system_prompt,
            judge_notes=judge_notes,
        )

        return self._score_rubric(prompt_text, checklist, model, model_params)

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

        parsed = _normalize_criteria_key(parse_json_safe(raw))

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
