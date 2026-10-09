"""Final trace/artifact cleanup handling.

Provides cleanup logic for the execute_runtime_test finally block,
including workspace cleanup, trace finalization, and artifact attachment.

This module owns:
    - Workspace cleanup (DDEV project or temp dir)
    - Trace finalization
    - Runtime artifact attachment to result

This module does NOT own:
    - RuntimeExecutionMixin class (see mixin.py)
    - execute_runtime_test orchestration (see flow.py)
    - Stage helpers (see stages.py)
    - Review nudge second-pass logic (see review_nudge.py)
    - Catastrophic failure short-circuit (see failure_shortcut.py)

Usage:
    Called by flow.py in the finally block of execute_runtime_test.
    Cleanup must never raise unexpectedly.
"""

from __future__ import annotations

import contextlib
import copy
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from nichebench.execution.diagnostics.trace import RuntimeTrace, first_failed_stage
from nichebench.execution.result import TestResult


def cleanup_workspace(
    workspace: Any,
    use_runtime_workspace: bool,
    workspace_dir: Optional[Path],
    runtime_timeout_seconds: int,
    keep_workspace: bool,
) -> None:
    """Clean up workspace resources.

    For runtime workspaces, calls workspace.cleanup() to release DDEV project
    resources (containers/networks/volumes) so repeated runs do not leak Docker
    networks. For temp workspaces, removes the temp directory.

    Cleanup is best-effort: any exception from workspace.cleanup() or shutil
    is suppressed so that cleanup never masks a real test result or prevents
    trace finalization.  This module's contract requires cleanup to never
    raise unexpectedly.

    Args:
        workspace: Workspace object (may be None if a validation failure
            prevented workspace from being assigned in the flow).
        use_runtime_workspace: Whether runtime workspace mode was active
        workspace_dir: Workspace directory path
        runtime_timeout_seconds: Timeout in seconds for cleanup
        keep_workspace: Whether to keep workspace (skip removal)
    """
    try:
        if use_runtime_workspace and workspace is not None and hasattr(workspace, "cleanup"):
            workspace.cleanup(
                timeout=runtime_timeout_seconds,
                remove_workspace=not keep_workspace,
            )
        else:
            if workspace_dir is not None:
                shutil.rmtree(workspace_dir, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - cleanup must never raise
        # Best-effort: log the cleanup error to stderr so it shows up in
        # run logs, but never propagate.  The real test result has already
        # been computed; cleanup is a teardown concern only.
        import sys

        print(
            f"[cleanup_workspace] WARNING: cleanup failed and was suppressed: {exc}",
            file=sys.stderr,
        )


def finalize_and_attach_trace(
    trace: RuntimeTrace,
    result: TestResult,
    diagnostics_enabled: bool,
) -> Dict[str, Any]:
    """Finalize runtime trace and attach to result artifacts.

    Args:
        trace: RuntimeTrace instance
        result: TestResult object
        diagnostics_enabled: Whether diagnostics are enabled

    Returns:
        Runtime trace payload dict
    """
    runtime_trace_payload = trace.finalize()
    if not result.runtime_artifacts:
        result.runtime_artifacts = {}
    if diagnostics_enabled:
        result.runtime_artifacts["runtime_trace.json"] = runtime_trace_payload

    first_failed = first_failed_stage(runtime_trace_payload)
    existing_meta = result.runtime_artifacts.get("metadata.json")
    if diagnostics_enabled and isinstance(existing_meta, dict):
        existing_meta["first_failed_stage"] = first_failed
    if diagnostics_enabled and isinstance(result.judge_output, dict):
        result.judge_output["first_failed_stage"] = first_failed

    return runtime_trace_payload


def stage_cleanup(trace: RuntimeTrace) -> None:
    """Execute cleanup stage.

    Args:
        trace: RuntimeTrace instance
    """
    open_stage = getattr(trace, "_open_stage", None)
    if open_stage is not None:
        trace.stage_end(open_stage, "failed", {"error": "stage left open before cleanup"})
    trace.stage_start("cleanup")
    trace.stage_end("cleanup", "passed")


def attach_capture_reference(capture: Any, trace: RuntimeTrace, result: Optional[TestResult] = None) -> None:
    """Attach the small capture descriptor to the runtime trace and artifacts.

    The descriptor points at the archive on disk; no payload is inlined, so
    ``runtime_trace.json`` and ``details.jsonl`` stay small while the artifact
    folder remains the visible entry point to the full journal.
    """
    if capture is None:
        return
    reference = capture.result_reference()
    if not reference:
        return
    # The early task pointer predates judge capture. Replace every result entry
    # only after finalise(None), from the same published descriptor snapshot.
    if result is not None:
        if result.runtime_artifacts is None:
            result.runtime_artifacts = {}
        result.runtime_artifacts["capture.json"] = copy.deepcopy(reference)
        metadata = result.runtime_artifacts.setdefault("metadata.json", {})
        if isinstance(metadata, dict):
            metadata["runtime_capture"] = copy.deepcopy(reference)
    with contextlib.suppress(Exception):
        trace.attach_capture(copy.deepcopy(reference))


def finalize_capture(capture: Any, current_stage: str) -> None:
    """Finalise the capture session before any state is deleted.

    Runs first in the ``finally`` block of ``execute_runtime_test`` so that a
    timeout, watchdog kill, non-zero exit, poll error, or ``KeyboardInterrupt``
    still leaves a complete archive. Ordering matters: the capture is finalised
    *before* ``cleanup_workspace`` removes the task workspace and *before* the
    trace is finalised, so the descriptor's completion state reflects reality.

    Never raises — a capture failure must not mask the real test result.
    """
    if capture is None:
        return
    try:
        if current_stage != "artifact_finalization":
            capture.note(f"run_ended_in_stage:{current_stage}")
        capture.finalise(None, f"stage_end:{current_stage}")
    except Exception as exc:  # noqa: BLE001 - finalisation must never raise
        import sys

        print(
            f"[finalize_capture] WARNING: capture finalisation failed and was suppressed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def application_capture_scope(capture: Any, lane: str = "judge", label: str = "") -> Any:
    """Bind the capture session as the application-boundary recorder for this thread.

    A no-op context manager when capture is disabled, so the classic task paths
    and the non-capture runtime path keep their current behaviour unchanged.
    The scope resets in a ``finally`` and never touches ``litellm.api_base``.
    """
    from nichebench.providers.litellm_client import app_capture_scope

    return app_capture_scope(capture, lane=lane, label=label)
