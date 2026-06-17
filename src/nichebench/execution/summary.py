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

from pathlib import Path
from typing import Any, Dict, List, Optional

from nichebench.execution.result import TestResult
from nichebench.utils.io import save_json


def categorize_result(result: TestResult) -> str:
    """Categorize a result as pass/partial/fail based on judge score.

    Args:
        result: Completed TestResult object.

    Returns:
        Category string: "pass", "partial", or "fail".
    """
    if result.category in ("code_generation", "bug_fixing"):
        judge_output = result.judge_output
        if isinstance(judge_output, dict):
            score = judge_output.get("overall_score", 0.0)
        else:
            score = 1.0 if result.passed else 0.0

        if score > 0.66:
            return "pass"
        if score >= 0.33:
            return "partial"
        return "fail"
    return "pass" if result.passed else "fail"


def _extract_runtime_score(result: TestResult) -> float:
    """Return the best numeric score available from a runtime TestResult.

    Prefers ``hybrid_score`` (deterministic + judge blended), then the
    deterministic-only or judge-only score, and finally the boolean pass
    flag as 1.0/0.0.  Never raises; returns 0.0 if no score is present.
    """
    judge_output = result.judge_output
    if isinstance(judge_output, dict):
        for key in ("hybrid_score", "final_score", "judge_score", "deterministic_score"):
            value = judge_output.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return 1.0 if result.passed else 0.0


def compute_average_score(results: List[TestResult], category: str) -> float:
    """Compute the average score across all results.

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
        elif category in ("code_generation", "bug_fixing", "code_agent"):
            judge_output = result.judge_output
            if isinstance(judge_output, dict):
                score = judge_output.get("overall_score", 0.0)
            else:
                score = 1.0 if result.passed else 0.0
        else:
            score = 1.0 if result.passed else 0.0
        total_score += score

    return total_score / len(results)


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

    categorized = [categorize_result(r) for r in results]
    passed_count = sum(1 for c in categorized if c == "pass")
    partial_count = sum(1 for c in categorized if c == "partial")
    failed_count = sum(1 for c in categorized if c == "fail")

    avg_score = compute_average_score(results, category)

    summary = {
        "framework": framework,
        "category": category,
        "model": mut_model_str,
        "judge": judge_model_str,
        "profile": profile,
        "config": {
            "mut": mut_runner_model_config,
            "judge": judge_runner_model_config,
            "evaluation": eval_config,
        },
        "total": len(results),
        "passed": passed_count,
        "partial": partial_count,
        "failed": failed_count,
        "avg_score": avg_score,
    }

    save_json(summary_path, summary)
