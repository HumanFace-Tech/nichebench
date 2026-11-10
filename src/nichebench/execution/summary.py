"""Summary aggregation helpers for NicheBench test execution.

This module owns:
    - update_summary: compute and write aggregate summary statistics
    - Helper functions for result categorization and scoring

This module does NOT own:
    - TestExecutor orchestration (see orchestrator.py)
    - Category routing (see dispatch.py)
    - Result persistence (see persistence.py)
    - Parallel execution (see parallel.py)
"""

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nichebench.config.settings import resolve_judge_threshold
from nichebench.execution.result import TestResult
from nichebench.metrics.types import JUDGE_ERROR_CONFIG
from nichebench.utils.io import save_json


def normalize_score(value: Any, default: float = 0.0) -> float:
    """Coerce a score-like value to a finite float in ``[0, 1]``.

    Shared coercion helper used by summary aggregation and CLI report
    rendering so both views agree on what counts as a valid score.

    Handles ``int``, ``float``, numeric strings (e.g. ``"0.95"``),
    and ``bool`` (which is an ``int`` subclass). Returns ``default`` for
    non-coercible values, ``bool`` inputs, non-finite floats and numeric
    strings (``nan``, ``inf``, ``-inf``), and any numeric or numeric-string
    outside the ``[0, 1]`` range.

    Args:
        value: The value to coerce.
        default: Fallback returned for invalid inputs.

    Returns:
        A finite float in ``[0, 1]`` or ``default``.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        result = float(value)
        if not math.isfinite(result):
            return default
        if result < 0.0 or result > 1.0:
            return default
        return result
    if isinstance(value, str):
        try:
            result = float(value)
            if not math.isfinite(result):
                return default
            if result < 0.0 or result > 1.0:
                return default
            return result
        except (ValueError, TypeError):
            return default
    return default


# Display-only partial band for code/bug results: a result that did not pass
# but scored at least this is reported as "partial" instead of "fail".  The
# pass/fail decision itself is ``result.passed`` (which already carries the
# configured ``evaluation.judge_threshold``), so the band never overrides it.
PARTIAL_SCORE_FLOOR = 0.33


def categorize_result(result: TestResult) -> str:
    """Categorize a result as pass/partial/fail.

    For ``quiz`` and everything outside code/bug the verdict is binary and
    driven by ``result.passed``.  For ``code_generation`` / ``bug_fixing``
    the pass flag is the single source of truth for pass/fail — a result
    with ``passed=False`` is never reported as ``"pass"`` no matter what
    score the judge output carries.  Partial is display-only: it applies
    only when the result did not pass but its score reaches
    ``PARTIAL_SCORE_FLOOR`` (``0.33``) and therefore shows real progress;
    everything below that is a fail.

    Args:
        result: Completed TestResult object.

    Returns:
        Category string: "pass", "partial", or "fail".
    """
    if result.category == "quiz":
        # Quiz verdicts are binary — never partial.
        return "pass" if result.passed else "fail"
    if result.category in ("code_generation", "bug_fixing"):
        if result.passed:
            return "pass"
        judge_output = result.judge_output
        score = normalize_score(judge_output.get("overall_score"), 0.0) if isinstance(judge_output, dict) else 0.0
        if score >= PARTIAL_SCORE_FLOOR:
            return "partial"
        return "fail"
    return "pass" if result.passed else "fail"


def _extract_runtime_score(result: TestResult) -> float:
    """Return the best numeric score available from a runtime TestResult.

    Prefers ``hybrid_score`` (deterministic + judge blended), then the
    deterministic-only or judge-only score, and finally the boolean pass
    flag as 1.0/0.0. Invalid score-like values in a higher-priority field —
    including booleans, non-finite floats, out-of-range floats, and numeric
    strings that fail range checks (``"nan"``, ``"inf"``, ``"1.5"``, etc.) —
    fall through to the next lower-priority field. The boolean ``passed``
    fallback applies only when no score-like value is present at all.
    """
    judge_output = result.judge_output
    if not isinstance(judge_output, dict):
        return 1.0 if result.passed else 0.0

    any_score_like_found = False
    for key in ("hybrid_score", "final_score", "judge_score", "deterministic_score"):
        value = judge_output.get(key)
        if isinstance(value, (bool, int, float)):
            any_score_like_found = True
            # Use nan as a sentinel: normalize_score never returns nan for valid input.
            score = normalize_score(value, default=math.nan)
            if not math.isnan(score):
                return score
        elif isinstance(value, str):
            try:
                float(value)  # probe; non-convertible → skip this key
            except (ValueError, TypeError):
                continue
            any_score_like_found = True
            score = normalize_score(value, default=math.nan)
            if not math.isnan(score):
                return score
    if any_score_like_found:
        return 0.0
    return 1.0 if result.passed else 0.0


def _extract_quiz_score(result: TestResult) -> float:
    """Return the binary score of a quiz result.

    Quiz verdicts carry a binary ``score`` (1.0 for a pass, 0.0 for a
    fail), which by construction agrees with ``result.passed``.  A value
    that is not binary is treated as unusable and the boolean verdict is
    used instead, so the average always equals the pass rate.
    """
    judge_output = result.judge_output
    if isinstance(judge_output, dict):
        raw = judge_output.get("score")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            score = normalize_score(raw, default=math.nan)
            if not math.isnan(score) and score in (0.0, 1.0):
                return score
    return 1.0 if result.passed else 0.0


def compute_average_score(results: List[TestResult], category: str) -> float:
    """Compute the average score across all results.

    For ``code_generation`` / ``bug_fixing`` the average is taken over
    ``judge_output["overall_score"]``; callers that separate judge-error
    rows (see :func:`update_summary`) must pass only scored results here.

    Args:
        results: List of completed TestResult objects.
        category: Task category for score interpretation.

    Returns:
        Average score as a float.
    """
    if not results:
        return 0.0

    total_score = 0.0
    for result in results:
        if category == "runtime":
            score = _extract_runtime_score(result)
        elif category == "quiz":
            score = _extract_quiz_score(result)
        elif category in ("code_generation", "bug_fixing", "code_agent"):
            judge_output = result.judge_output
            if isinstance(judge_output, dict):
                score = normalize_score(judge_output.get("overall_score"), 0.0)
            else:
                score = 1.0 if result.passed else 0.0
        else:
            score = 1.0 if result.passed else 0.0
        total_score += score

    return total_score / len(results)


def _resolve_judge_method(results: List[TestResult]) -> str:
    """Return the scoring method used for the run.

    The first result that carries a method wins; ``llm`` is the fallback.
    Quiz results carry ``llm`` too — the judge LLM is their authority.
    """
    for result in results:
        method = getattr(result, "judge_method", None)
        if method:
            return str(method)
    return "llm"


def _resolve_dataset_version(results: List[TestResult]) -> Optional[str]:
    """Return the first dataset version carried by the results (or ``None``)."""
    for result in results:
        version = getattr(result.test_case, "dataset_version", None)
        if version:
            return str(version)
    return None


def _collect_parse_failures(results: List[TestResult]) -> Tuple[int, Dict[str, int]]:
    """Count quiz results whose parser cross-check found no single letter.

    The parser runs on every quiz answer as an audit cross-check (it never
    decides the score); a cross-check that could not select a letter is a
    parse failure.  Returns a ``(count, breakdown)`` tuple where
    ``breakdown`` maps a parse-failure reason to the number of results
    carrying it.
    """
    breakdown: Dict[str, int] = {}
    count = 0
    for result in results:
        judge_output = result.judge_output
        if not isinstance(judge_output, dict):
            continue
        reason = judge_output.get("parse_reason")
        if not reason or reason == "ok":
            continue
        count += 1
        key = str(reason)
        breakdown[key] = breakdown.get(key, 0) + 1
    return count, breakdown


def _is_judge_error(result: TestResult) -> bool:
    """Return ``True`` when the result carries a ``judge_*`` error kind.

    Such rows are judge infrastructure failures: they must be counted
    separately and excluded from scoring (see :func:`update_summary`).
    """
    kind = getattr(result, "error_kind", None)
    return isinstance(kind, str) and kind.startswith("judge_")


def _is_config_error(result: TestResult) -> bool:
    """Return ``True`` when the result carries the ``config_error`` kind.

    Such rows are harness/configuration failures (an exception that escaped
    the runners, an unknown category): like judge errors they say nothing
    about the model, so they are counted separately — in ``config_errors``,
    never folded into ``judge_errors`` — and excluded from scoring (see
    :func:`update_summary`).
    """
    kind = getattr(result, "error_kind", None)
    return kind == JUDGE_ERROR_CONFIG


def update_summary(
    results: List[TestResult],
    summary_path: Path,
    category: str,
    framework: str,
    mut_model_str: str,
    judge_model_str: str,
    mut_runner_model_config: Dict[str, Any],
    judge_runner_model_config: Dict[str, Any],
    profile: Optional[str],
    eval_config: Dict[str, Any],
) -> None:
    """Compute and write aggregate summary statistics to summary.json.

    Aggregates pass/partial/fail counts and average score across all results,
    then serializes the full summary including model configs and eval config.

    Results whose ``error_kind`` starts with ``judge_`` are judge
    infrastructure failures and results whose ``error_kind`` is
    ``config_error`` are harness/configuration failures: both say nothing
    about the model, so both are counted in ``attempted_total`` and in their
    own bucket (``judge_errors`` / ``config_errors``) but excluded from
    ``total``, the pass/partial/fail counts and ``avg_score``.  The two
    buckets stay separate — a configuration failure is never reported as a
    judge failure.  ``total`` therefore equals the scored count,
    ``attempted_total`` the raw count, and ``avg_score`` is ``None`` when
    nothing could be scored.

    For the ``quiz`` category the counts and ``avg_score`` describe the
    judge verdict: ``avg_score`` is the pass rate over the scored rows,
    ``partial`` is always ``0``, and the parser cross-check taxonomy is
    reported alongside.  The reported judge *configuration* is the real,
    configured judge — quiz is judged by an LLM like every other category.

    For the LLM-judged categories the reported ``config.evaluation`` block
    always carries the effective ``judge_threshold`` — the configured value,
    or the environment default when none is configured — so a later report
    renders the run with the bar the run actually used.

    Args:
        results: List of completed TestResult objects.
        summary_path: Destination path for summary.json.
        category: Task category.
        framework: Framework name.
        mut_model_str: MUT model string.
        judge_model_str: Judge model string.
        mut_runner_model_config: MUT runner model configuration dict.
        judge_runner_model_config: Judge runner model configuration dict.
        profile: Active profile name (or None).
        eval_config: Evaluation configuration dict.
    """
    if not results:
        return

    judge_error_results = [r for r in results if _is_judge_error(r)]
    config_error_results = [r for r in results if _is_config_error(r)]
    excluded_ids = {id(r) for r in judge_error_results} | {id(r) for r in config_error_results}
    scored_results = [r for r in results if id(r) not in excluded_ids]

    categorized = [categorize_result(r) for r in scored_results]
    passed_count = sum(1 for c in categorized if c == "pass")
    partial_count = sum(1 for c in categorized if c == "partial")
    failed_count = sum(1 for c in categorized if c == "fail")

    avg_score: Optional[float] = compute_average_score(scored_results, category) if scored_results else None

    parse_failures, parse_failure_reasons = _collect_parse_failures(scored_results)

    # Record the effective pass threshold for the LLM-judged categories, so a
    # later report renders the run with the bar the run actually used even if
    # the environment default changes in between.  Quiz verdicts are
    # exact-match and advertise no judge threshold.
    evaluation_config = dict(eval_config)
    if category in ("code_generation", "bug_fixing", "code_agent"):
        evaluation_config["judge_threshold"] = resolve_judge_threshold(evaluation_config)

    summary = {
        "framework": framework,
        "category": category,
        "model": mut_model_str,
        "judge": judge_model_str,
        "judge_method": _resolve_judge_method(results),
        "dataset_version": _resolve_dataset_version(results),
        "profile": profile,
        "config": {
            "mut": mut_runner_model_config,
            "judge": judge_runner_model_config,
            "evaluation": evaluation_config,
        },
        "total": len(scored_results),
        "attempted_total": len(results),
        "judge_errors": len(judge_error_results),
        "config_errors": len(config_error_results),
        "passed": passed_count,
        "partial": partial_count,
        "failed": failed_count,
        "avg_score": avg_score,
        "parse_failures": parse_failures,
        "parse_failure_reasons": parse_failure_reasons,
    }

    save_json(summary_path, summary)
