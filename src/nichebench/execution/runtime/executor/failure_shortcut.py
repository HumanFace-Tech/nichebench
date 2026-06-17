"""Catastrophic failure short-circuit handling.

Provides early-exit logic when the agent crashes or produces no meaningful
work before deterministic checks would give misleading signal.

This module owns:
    - Catastrophic failure detection
    - Short-circuit result construction
    - Trace stage skipping for checks/judge

This module does NOT own:
    - RuntimeExecutionMixin class (see mixin.py)
    - execute_runtime_test orchestration (see flow.py)
    - Stage helpers (see stages.py)
    - Review nudge second-pass logic (see review_nudge.py)
    - Cleanup handling (see cleanup.py)

Usage:
    Called by flow.py after agent_execution when catastrophic failure
    is detected. Bails out early and marks checks/judge as skipped.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from nichebench.execution.diagnostics.trace import (
    RuntimeTrace,
    classify_runtime_failure,
)
from nichebench.execution.result import TestResult


def detect_catastrophic_failure(
    mixin: Any,
    mut_output: str,
    run_log: str,
    trajectory: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Detect catastrophic agent failure.

    Returns a reason string when the agent crashed at startup or timed out
    before doing any meaningful work. Running deterministic checks or the
    judge on an empty workspace would produce misleading low-quality signal.

    Args:
        mixin: RuntimeExecutionMixin instance
        mut_output: MUT output string
        run_log: Run log string
        trajectory: Trajectory dict or None

    Returns:
        Reason string if catastrophic, None otherwise
    """
    return mixin._detect_catastrophic_failure(mut_output, run_log, trajectory)


def apply_failure_shortcut(
    result: TestResult,
    catastrophic_reason: str,
    diagnostics_enabled: bool,
    runtime_artifacts: Dict[str, Any],
    trace: RuntimeTrace,
) -> None:
    """Apply catastrophic failure short-circuit: skip checks/judge, mark result failed.

    Args:
        result: TestResult object (modified in place)
        catastrophic_reason: Reason string from detection
        diagnostics_enabled: Whether diagnostics are enabled
        runtime_artifacts: Runtime artifacts dict (modified in place)
        trace: RuntimeTrace instance
    """
    result.passed = False
    failure_info = classify_runtime_failure(
        error=catastrophic_reason,
        failed_critical_check=False,
        failed_stage="agent_execution",
    )
    result.judge_output = {
        "catastrophic_failure": True,
        "error": catastrophic_reason,
        "failure_reason": catastrophic_reason,
    }
    if diagnostics_enabled:
        result.judge_output.update(failure_info.to_dict())

    metadata = runtime_artifacts.get("metadata.json") or {}
    metadata = dict(metadata)
    if diagnostics_enabled:
        metadata.update(failure_info.to_dict())
    runtime_artifacts["metadata.json"] = metadata

    # Skip deterministic_checks stage
    trace.stage_start("deterministic_checks")
    trace.stage_end("deterministic_checks", "skipped", {"reason": "catastrophic_failure"})

    # Skip judge_scoring stage
    trace.stage_start("judge_scoring")
    trace.stage_end("judge_scoring", "skipped", {"reason": "catastrophic_failure"})

    # Pass through artifact_finalization
    trace.stage_start("artifact_finalization")
    trace.stage_end("artifact_finalization", "passed")


def reapply_ddev_project_name(workspace: Any) -> None:
    """Re-apply DDEV project name in case the MUT restored .ddev/config.yaml.

    Without this, scoring ddev commands fail with "No running container found
    for service 'web' in 'nichejobs' project".

    Args:
        workspace: Workspace object
    """
    if hasattr(workspace, "_ensure_preconfigured_ddev_project_name"):
        workspace._ensure_preconfigured_ddev_project_name()
