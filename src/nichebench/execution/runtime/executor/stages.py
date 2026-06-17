"""Stage helpers for execute_runtime_test.

Provides small, docstringed helpers for each stage of the runtime execution flow.

This module owns:
    - Stage helper functions that encapsulate per-stage logic
    - Config resolution helpers
    - Workspace setup helpers
    - Bootstrap helpers
    - Deterministic check helpers
    - Judge scoring helpers
    - Artifact finalization helpers

This module does NOT own:
    - RuntimeExecutionMixin class (see mixin.py)
    - execute_runtime_test orchestration (see flow.py)
    - Review nudge second-pass logic (see review_nudge.py)
    - Catastrophic failure short-circuit (see failure_shortcut.py)
    - Cleanup handling (see cleanup.py)

Usage:
    These helpers are called by flow.py's execute_runtime_test. They are
    designed to be small, focused functions that can be tested in isolation.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.diagnostics.trace import (
    RuntimeTrace,
    classify_runtime_failure,
)
from nichebench.execution.result import TestResult
from nichebench.execution.runtime.scoring import CheckResult


def resolve_runtime_config(runtime_config: Dict[str, Any]) -> Tuple[int, bool, str]:
    """Resolve runtime configuration values.

    Args:
        runtime_config: Runtime configuration dict

    Returns:
        Tuple of (runtime_timeout_seconds, keep_workspace, effective_runtime_mode)
    """
    if runtime_config.get("runtime_timeout_seconds") is not None:
        runtime_timeout_seconds = int(runtime_config.get("runtime_timeout_seconds", 1800))
    elif runtime_config.get("runtime_timeout_minutes") is not None:
        runtime_timeout_seconds = int(runtime_config.get("runtime_timeout_minutes", 30)) * 60
    else:
        runtime_timeout_seconds = 1800

    keep_workspace = bool(runtime_config.get("runtime_keep_workspaces", False))
    runtime_mode = str(runtime_config.get("runtime_mode", "cage"))
    effective_runtime_mode = "cage" if runtime_mode in ("cage", "container") else runtime_mode

    return runtime_timeout_seconds, keep_workspace, effective_runtime_mode


def resolve_workspace_mode(test_case: TestCaseSpec) -> bool:
    """Determine if runtime workspace mode should be used.

    Args:
        test_case: Test case specification

    Returns:
        True if use_runtime_workspace (source and environment dicts present)
    """
    source = test_case.raw.get("source") if test_case.raw else None
    environment = test_case.raw.get("environment") if test_case.raw else None
    return isinstance(source, dict) and isinstance(environment, dict)


def stage_config_resolution(
    trace: RuntimeTrace,
    runtime_config: Dict[str, Any],
) -> None:
    """Execute config_resolution stage.

    Args:
        trace: RuntimeTrace instance
        runtime_config: Runtime configuration dict
    """
    current_stage = "config_resolution"
    trace.stage_start(current_stage, {"runtime_mode": runtime_config.get("runtime_mode", "cage")})
    trace.stage_end("config_resolution", "passed")


def stage_workspace_setup(
    trace: RuntimeTrace,
    use_runtime_workspace: bool,
    workspace: Any,
    test_case: TestCaseSpec,
    repo_root: Optional[Path],
    runtime_timeout_seconds: int,
    pre_existing_workspace_dir: Optional[Path] = None,
) -> Path:
    """Execute workspace_setup stage.

    Args:
        trace: RuntimeTrace instance
        use_runtime_workspace: Whether runtime workspace mode is active
        workspace: Workspace object
        test_case: Test case specification
        repo_root: Git repository root path (required if use_runtime_workspace is True)
        runtime_timeout_seconds: Timeout in seconds
        pre_existing_workspace_dir: Pre-existing workspace_dir for non-runtime-workspace mode

    Returns:
        workspace_dir path
    """
    current_stage = "workspace_setup"
    trace.stage_start(current_stage, {"use_runtime_workspace": use_runtime_workspace})
    workspace_dir: Optional[Path] = None

    if use_runtime_workspace:
        environment = test_case.raw.get("environment")
        assert isinstance(environment, dict)
        setup_mode = str(environment.get("setup_mode", "config_import"))
        post_setup_commands = environment.get("post_setup_commands")
        workspace.create(source_path=repo_root, sha=test_case.resolved_sha)
        workspace.ddev_start(
            setup_mode=setup_mode,
            timeout=runtime_timeout_seconds,
            post_setup_commands=post_setup_commands if isinstance(post_setup_commands, list) else None,
        )
        workspace._run_logged_command(["ddev", "status"], timeout=runtime_timeout_seconds)
        workspace._run_logged_command(
            ["ddev", "drush", "status", "--fields=bootstrap,drupal-version"],
            timeout=runtime_timeout_seconds,
        )
        workspace_dir = workspace.path
    else:
        workspace_dir = pre_existing_workspace_dir

    trace.stage_end("workspace_setup", "passed", {"workspace_path": str(workspace_dir)})
    assert workspace_dir is not None
    return workspace_dir


def stage_environment_bootstrap(
    trace: RuntimeTrace,
    workspace_dir: Path,
    runtime_config: Dict[str, Any],
    runtime_mode: str,
    effective_runtime_mode: str,
    mixin: Any,
    test_case: TestCaseSpec,
) -> Optional[Path]:
    """Execute environment_bootstrap stage.

    Args:
        trace: RuntimeTrace instance
        workspace_dir: Workspace directory path (must not be None)
        runtime_config: Runtime configuration dict
        runtime_mode: Raw runtime mode string
        effective_runtime_mode: Effective runtime mode
        mixin: RuntimeExecutionMixin instance
        test_case: Test case specification

    Returns:
        runtime_hints_path or None
    """
    current_stage = "environment_bootstrap"
    trace.stage_start(current_stage)

    assert workspace_dir is not None  # always set by this point
    mixin._run_runtime_preflight_host(runtime_config, runtime_mode)
    mixin._run_runtime_preflight_workspace(workspace_dir, effective_runtime_mode)
    mixin._inject_task_markdown(workspace_dir, test_case)
    runtime_hints_path = mixin._inject_runtime_hints(workspace_dir, test_case)

    trace.stage_end("environment_bootstrap", "passed")
    return runtime_hints_path


def build_checks_payload(check_results: List[CheckResult]) -> List[Dict[str, Any]]:
    """Build checks payload for artifact storage.

    Args:
        check_results: List of CheckResult objects

    Returns:
        List of check dicts suitable for JSON serialization
    """
    return [
        {
            "name": c.name,
            "type": c.type,
            "passed": c.passed,
            "message": c.message,
            "critical": c.is_critical,
            "details": c.details,
        }
        for c in check_results
    ]


def stage_deterministic_checks(
    trace: RuntimeTrace,
    check_results: List[CheckResult],
) -> None:
    """Execute deterministic_checks stage.

    Args:
        trace: RuntimeTrace instance
        check_results: List of CheckResult objects
    """
    current_stage = "deterministic_checks"
    trace.stage_start(current_stage)

    trace.stage_end(
        "deterministic_checks",
        "passed",
        {
            "total_checks": len(check_results),
            "failed_checks": len([c for c in check_results if not c.passed]),
        },
    )


def run_judge_scoring(
    test_case: TestCaseSpec,
    user_input: str,
    mut_output: str,
    judge_runner: Any,
    judge_system_prompt: Optional[str],
    runtime_artifacts: Dict[str, Any],
    runtime_config: Dict[str, Any],
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Run LLM judge scoring with optional multi-sample median.

    Args:
        test_case: Test case specification
        user_input: User input string
        mut_output: MUT output string
        judge_runner: Judge runner instance
        judge_system_prompt: Optional judge system prompt
        runtime_artifacts: Runtime artifacts dict
        runtime_config: Runtime configuration dict

    Returns:
        Tuple of (judge_score or None, runtime_judge_output dict)
    """
    judge_score: Optional[float] = None
    runtime_judge_output: Dict[str, Any] = {}
    llm_judge_config = test_case.raw.get("llm_judge", {})

    if llm_judge_config.get("checklist"):
        judge_samples = int(runtime_config.get("runtime_judge_samples", 1))
        judge_samples = max(1, judge_samples)

        sample_scores: List[float] = []
        last_judge_output: Dict[str, Any] = {}
        for _ in range(judge_samples):
            last_judge_output, _ = judge_runner.evaluate_test(
                test_case,
                "runtime",
                user_input,
                mut_output,
                judge_system_prompt,
                runtime_artifacts=runtime_artifacts,
            )
            sample_scores.append(_coerce_judge_score(last_judge_output))

        judge_score = statistics.median(sample_scores)
        runtime_judge_output = last_judge_output
        if judge_samples > 1:
            runtime_judge_output["judge_sample_scores"] = sample_scores
            runtime_judge_output["judge_sample_median"] = judge_score

    return judge_score, runtime_judge_output


def _coerce_judge_score(judge_output: Dict[str, Any]) -> float:
    """Coerce judge output ``overall_score`` to a clamped float, tolerant of malformed LLM output.

    LLM judge output is explicitly allowed to be malformed. ``"N/A"``, ``None``,
    a missing ``overall_score`` key, or a value outside ``[0.0, 1.0]`` are coerced
    to a conservative score (0.0) and the malformed value is recorded in
    ``judge_output["malformed_score"]`` so downstream forensics can see it.

    Args:
        judge_output: Judge output dict (mutated in place to add diagnostic fields).

    Returns:
        A float in ``[0.0, 1.0]``; 0.0 if the score is missing/malformed.
    """
    if not isinstance(judge_output, dict):
        return 0.0
    raw = judge_output.get("overall_score", 0.0)
    try:
        score = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        judge_output["malformed_score"] = raw
        return 0.0
    if score < 0.0 or score > 1.0:
        judge_output["malformed_score"] = score
        return 0.0
    return score


def stage_judge_scoring(
    trace: RuntimeTrace,
    hybrid_score: Any,
) -> None:
    """Execute judge_scoring stage.

    Args:
        trace: RuntimeTrace instance
        hybrid_score: HybridScore object with deterministic/judge/final scores
    """
    current_stage = "judge_scoring"
    trace.stage_start(current_stage)

    trace.stage_end(
        "judge_scoring",
        "passed",
        {
            "deterministic_score": hybrid_score.deterministic_score,
            "judge_score": hybrid_score.judge_score,
            "hybrid_score": hybrid_score.final_score,
        },
    )


def stage_artifact_finalization(
    trace: RuntimeTrace,
    result: TestResult,
    check_results: List[CheckResult],
    diagnostics_enabled: bool,
    runtime_artifacts: Dict[str, Any],
) -> None:
    """Execute artifact_finalization stage.

    Args:
        trace: RuntimeTrace instance
        result: TestResult object
        check_results: List of CheckResult objects
        diagnostics_enabled: Whether diagnostics are enabled
        runtime_artifacts: Runtime artifacts dict
    """
    current_stage = "artifact_finalization"
    trace.stage_start(current_stage)

    failed_critical_check = any(c.is_critical and not c.passed for c in check_results)
    failure_info = classify_runtime_failure(
        error=result.error,
        failed_critical_check=failed_critical_check,
        failed_stage="none",
    )
    metadata = runtime_artifacts.get("metadata.json") or {}
    metadata = dict(metadata)
    if diagnostics_enabled:
        metadata.update(failure_info.to_dict())
    runtime_artifacts["metadata.json"] = metadata
    if diagnostics_enabled:
        result.judge_output["failure_class"] = metadata.get("failure_class")
        result.judge_output["failure_code"] = metadata.get("failure_code")
        result.judge_output["failure_fingerprint"] = metadata.get("failure_fingerprint")

    trace.stage_end("artifact_finalization", "passed")


def handle_exception(
    trace: RuntimeTrace,
    current_stage: str,
    result: TestResult,
    workspace: Any,
    runtime_timeout_seconds: int,
    keep_workspace: bool,
    use_runtime_workspace: bool,
    diagnostics_enabled: bool,
    exc: Exception,
) -> None:
    """Handle exceptions during execute_runtime_test.

    Args:
        trace: RuntimeTrace instance
        current_stage: Current stage name when exception occurred
        result: TestResult object
        workspace: Workspace object
        runtime_timeout_seconds: Timeout in seconds
        keep_workspace: Whether to keep workspace
        use_runtime_workspace: Whether runtime workspace mode is active
        diagnostics_enabled: Whether diagnostics are enabled
        exc: The exception that was raised
    """
    failed_stage = getattr(trace, "_open_stage", None) or current_stage
    if getattr(trace, "_open_stage", None) is not None:
        trace.stage_end(failed_stage, "failed", {"error": str(exc)})
    result.error = str(exc)
    result.passed = False
    result.mut_output = f"[Error: {exc}]"
    result.judge_output = {"catastrophic_failure": True, "error": str(exc)}
    failure_info = classify_runtime_failure(
        error=str(exc),
        failed_critical_check=False,
        failed_stage=failed_stage,
    )
    if not result.runtime_artifacts:
        result.runtime_artifacts = {}

    # Best-effort salvage of cage-side artifacts for timeout/catastrophic failures.
    # workspace may be None if the exception happened during workspace_setup
    # before workspace was assigned; tolerate that case so handle_exception()
    # never raises UnboundLocalError and overwrites the real exception.
    if workspace is not None:
        try:
            run_artifacts_path = getattr(workspace, "run_artifacts_path", None)
            if run_artifacts_path:
                rap = Path(run_artifacts_path)
                run_log_path = rap / "run.log"
                partial_traj_path = rap / "opencode_partial_trajectory.json"
                session_dump_path = rap / "opencode_session_dump.json"

                if run_log_path.exists():
                    result.runtime_artifacts["run.log"] = run_log_path.read_text(encoding="utf-8", errors="replace")
                if partial_traj_path.exists():
                    result.runtime_artifacts["opencode_partial_trajectory.json"] = json.loads(
                        partial_traj_path.read_text(encoding="utf-8")
                    )
                if session_dump_path.exists():
                    result.runtime_artifacts["opencode_session_dump.json"] = json.loads(
                        session_dump_path.read_text(encoding="utf-8")
                    )
        except Exception:
            pass

    meta = result.runtime_artifacts.get("metadata.json") or {}
    meta = dict(meta)
    if diagnostics_enabled:
        meta.update(failure_info.to_dict())
    result.runtime_artifacts["metadata.json"] = meta
    if diagnostics_enabled:
        result.judge_output.update(
            {
                "failure_class": meta.get("failure_class"),
                "failure_code": meta.get("failure_code"),
                "failure_fingerprint": meta.get("failure_fingerprint"),
            }
        )


def finalize_trace(
    trace: RuntimeTrace,
    result: TestResult,
    diagnostics_enabled: bool,
) -> Dict[str, Any]:
    """Finalize the runtime trace and attach to result.

    Args:
        trace: RuntimeTrace instance
        result: TestResult object
        diagnostics_enabled: Whether diagnostics are enabled

    Returns:
        Runtime trace payload dict
    """
    from nichebench.execution.diagnostics.trace import first_failed_stage

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
