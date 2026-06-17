"""Second-pass review nudge logic.

Provides the conditional second-pass execution that delivers a review nudge
as a fresh user message when the first pass produced meaningful work.

This module owns:
    - Second-pass review nudge execution
    - First-pass meaningful-work detection
    - Review pass info construction

This module does NOT own:
    - RuntimeExecutionMixin class (see mixin.py)
    - execute_runtime_test orchestration (see flow.py)
    - Stage helpers (see stages.py)
    - Catastrophic failure short-circuit (see failure_shortcut.py)
    - Cleanup handling (see cleanup.py)

Usage:
    Called by flow.py during the agent_execution stage when
    runtime_enable_review_nudge is True and first pass was meaningful.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec


def detect_meaningful_first_pass(
    mut_output: str,
    trajectory: Optional[Dict[str, Any]],
    run_log: str,
) -> bool:
    """Detect whether the first MUT pass produced meaningful work.

    A meaningful pass is one where the agent produced non-empty output,
    made tool calls, or logged command executions. This prevents the review
    nudge from simply repeating non-working behaviour.

    Args:
        mut_output: First pass MUT output
        trajectory: First pass trajectory dict or None
        run_log: First pass run log

    Returns:
        True if the first pass did meaningful work
    """
    has_output = bool(mut_output and mut_output.strip())
    has_tool_calls = bool(trajectory and any(msg.get("tool_calls") for msg in trajectory.get("messages", [])))
    has_commands = bool(run_log and ("$ " in run_log or "→ " in run_log))

    return has_output or has_tool_calls or has_commands


def build_review_pass_info(
    first_pass_mut_output: str,
    first_pass_run_log: str,
) -> Dict[str, Any]:
    """Build initial review pass info dict.

    Args:
        first_pass_mut_output: Output from first MUT pass
        first_pass_run_log: Run log from first MUT pass

    Returns:
        Review pass info dict with attempted=True
    """
    return {
        "first_pass_output": first_pass_mut_output,
        "first_pass_run_log": first_pass_run_log,
        "attempted": True,
    }


def run_review_nudge_pass(
    mixin: Any,
    test_case: TestCaseSpec,
    workspace: Any,
    runtime_config: Dict[str, Any],
    profile: Any,
    runtime_timeout_seconds: int,
    review_nudge: str,
) -> Tuple[str, str, str, Dict[str, Any], Optional[str], Optional[Dict[str, Any]], Any]:
    """Execute the second MUT pass with review nudge as task_input_override.

    The review nudge is delivered as a fresh user message (NOT appended to
    TASK.md) to give the agent a chance to self-reflect and improve.

    Args:
        mixin: RuntimeExecutionMixin instance
        test_case: Test case specification
        workspace: Workspace object
        runtime_config: Runtime configuration dict
        profile: Profile object
        runtime_timeout_seconds: Timeout in seconds
        review_nudge: Review nudge text

    Returns:
        Tuple of (mut_output, user_input, run_log, island_topology,
                 effective_image, trajectory, retry_info)
    """
    return mixin._run_container_runtime_task_with_retry(
        test_case=test_case,
        workspace=workspace,
        agent_manifest=test_case.raw.get("agent", {}),
        runtime_config=runtime_config,
        profile=profile,
        timeout_seconds=runtime_timeout_seconds,
        task_input_override=review_nudge,
    )


def handle_review_pass_failure(
    review_pass_info: Dict[str, Any],
    first_pass_mut_output: str,
    first_pass_run_log: str,
    exc: Exception,
) -> Tuple[str, str]:
    """Handle review pass failure by preserving first-pass output.

    A review nudge is a quality pass, not the primary task execution.
    We preserve first-pass output and continue to deterministic checks
    instead of classifying the entire run as catastrophic.

    Args:
        review_pass_info: Review pass info dict (modified in place)
        first_pass_mut_output: Output from first MUT pass
        first_pass_run_log: Run log from first MUT pass
        exc: Exception from failed review pass

    Returns:
        Tuple of (mut_output, run_log) to use going forward
    """
    review_pass_info["failed"] = True
    review_pass_info["error"] = str(exc)
    return first_pass_mut_output, first_pass_run_log


def should_run_review_nudge(
    enable_review_nudge: bool,
    effective_runtime_mode: str,
    first_pass_meaningful: bool,
) -> bool:
    """Determine if review nudge second pass should run.

    Args:
        enable_review_nudge: Whether review nudge is enabled in config
        effective_runtime_mode: Effective runtime mode (cage/host)
        first_pass_meaningful: Whether first pass did meaningful work

    Returns:
        True if review nudge pass should be attempted
    """
    return enable_review_nudge and effective_runtime_mode == "cage" and first_pass_meaningful
