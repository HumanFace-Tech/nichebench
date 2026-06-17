"""Cage container retry logic.

**Ownership**: This module owns the auto-retry behavior for rejected tool
attempts and schema/JSON parse failures in the cage container runtime.

**Explicitly does not own**:
- Island topology (see ``islands``)
- Docker command assembly (see ``docker_args``)
- Subprocess launch (see ``process_io``)

**Container safety constraints**:
- Retry only triggers on specific error patterns (invalid_request_error + rejected tools)
- Maximum retry attempts controlled by ``runtime_tool_retry_attempts`` (default 3)
- TASK.md is appended with retry guidance on rejected tool attempts (best-effort)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec

# Tools that must be used with exact parameter names to avoid schema mismatches
RETRY_APPENDIX = (
    " IMPORTANT: You attempted a tool that is not in the allowed list or "
    "called it with wrong parameters. Use exact tool names: read, write, edit, bash. "
    "IMPORTANT: When calling 'read', you MUST provide the 'filePath' parameter "
    "(and optionally 'offset' and 'limit'). "
    "Do not call any tool not in {read, write, edit, bash}. Continue the task."
)


def should_retry_run(
    run_log: str,
    parse_rejected_tool_attempts_fn: Any,
) -> Tuple[bool, Optional[str], bool]:
    """Determine if a retry should be attempted based on run log.

    Args:
        run_log: Combined stdout/stderr from the run.
        parse_rejected_tool_attempts_fn: Callable to parse rejected tool attempts.

    Returns:
        Tuple of (should_retry, reason, is_json_parse_failure).
        should_retry is False if no retry should occur.
    """
    if not run_log or "invalid_request_error" not in run_log.lower():
        return False, None, False

    rejected = parse_rejected_tool_attempts_fn(run_log)
    json_parse_in_log = "Failed to parse tool call arguments as JSON" in run_log
    if not (rejected or json_parse_in_log):
        return False, None, False

    is_json_parse_class = False
    json_parse_tool_names = {"json", "parse", "arguments", "tool_call"}
    if rejected:
        for r in rejected:
            tn = r.get("tool_name", "")
            if tn and any(pattern in tn for pattern in json_parse_tool_names):
                is_json_parse_class = True
                break
    elif json_parse_in_log:
        is_json_parse_class = True

    if is_json_parse_class:
        return True, "json_parse_failure", True
    reason = f"rejected tool attempts: {[r['tool_name'] for r in rejected]}"
    return True, reason, False


def append_retry_guidance_to_task_md(
    workspace_host_path: Path,
    retry_appendix: str = RETRY_APPENDIX,
) -> None:
    """Append retry guidance to TASK.md in the workspace.

    Best-effort; silently ignores OSError if TASK.md cannot be updated.

    Args:
        workspace_host_path: Resolved host path to workspace.
        retry_appendix: Guidance text to append.
    """
    task_md_path = workspace_host_path / "TASK.md"
    try:
        existing_task_md = task_md_path.read_text(encoding="utf-8").strip()
        task_md_path.write_text(existing_task_md + retry_appendix, encoding="utf-8")
    except OSError:
        pass  # If we can't update TASK.md, proceed with retry anyway


def build_retry_info(
    attempted: bool,
    reason: str,
    count: int,
) -> Dict[str, Any]:
    """Build the retry info dict for metadata.

    Args:
        attempted: Whether retry was attempted.
        reason: Reason for retry.
        count: Retry attempt number.

    Returns:
        Retry info dict.
    """
    return {
        "attempted": attempted,
        "reason": reason,
        "count": count,
    }


def get_max_retry_attempts(runtime_config: Dict[str, Any]) -> int:
    """Get maximum retry attempts from runtime config.

    Args:
        runtime_config: Full runtime configuration dict.

    Returns:
        Maximum retry attempts (minimum 0).
    """
    return max(int(runtime_config.get("runtime_tool_retry_attempts", 3)), 0)


def execute_retry_loop(
    first_run_result: Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]]],
    test_case: TestCaseSpec,
    workspace: Any,
    agent_manifest: Dict[str, Any],
    runtime_config: Dict[str, Any],
    profile: Any,
    timeout_seconds: int,
    task_input_override: Optional[str],
    run_container_task_fn: Any,
    parse_rejected_tool_attempts_fn: Any,
    max_retry_attempts: int,
) -> Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Execute the retry loop for container runtime tasks.

    This function encapsulates the retry logic that was formerly in
    ``_run_container_runtime_task_with_retry``.

    Args:
        first_run_result: Result tuple from first ``_run_container_runtime_task`` call.
        test_case: Test case specification.
        workspace: Workspace instance.
        agent_manifest: Agent configuration.
        runtime_config: Runtime configuration dict.
        profile: Resolved profile object.
        timeout_seconds: Hard timeout in seconds.
        task_input_override: Optional task input override.
        run_container_task_fn: Callable for ``_run_container_runtime_task``.
        parse_rejected_tool_attempts_fn: Callable for ``_parse_rejected_tool_attempts``.
        max_retry_attempts: Maximum number of retry attempts.

    Returns:
        Tuple of (mut_output, user_input, run_log, island_topology, effective_image,
        trajectory, retry_info).
    """
    mut_output, user_input, run_log, island_topology, effective_image, trajectory = first_run_result

    retry_info: Optional[Dict[str, Any]] = None
    retry_attempts = 0

    while retry_attempts < max_retry_attempts:
        should_retry, retry_trigger_reason, is_json_parse = should_retry_run(run_log, parse_rejected_tool_attempts_fn)
        if not should_retry:
            break

        # reason is guaranteed non-None when should_retry is True
        assert retry_trigger_reason is not None
        current_task_input_override = task_input_override
        if not is_json_parse:
            # Append retry guidance to task_input_override or TASK.md
            if task_input_override is not None:
                current_task_input_override = task_input_override.rstrip() + RETRY_APPENDIX
            else:
                workspace_host_path = (
                    Path(workspace.path).resolve() if hasattr(workspace, "path") else Path(workspace.path).resolve()
                )
                append_retry_guidance_to_task_md(workspace_host_path)

        retry_attempts += 1
        retry_info = build_retry_info(
            attempted=True,
            reason=retry_trigger_reason,
            count=retry_attempts,
        )

        (
            mut_output_retry,
            user_input_retry,
            run_log_retry,
            island_topology_retry,
            effective_image_retry,
            trajectory_retry,
        ) = run_container_task_fn(
            test_case=test_case,
            workspace=workspace,
            agent_manifest=agent_manifest,
            runtime_config=runtime_config,
            profile=profile,
            timeout_seconds=timeout_seconds,
            task_input_override=current_task_input_override,
        )
        mut_output = mut_output_retry
        user_input = user_input_retry
        run_log = run_log_retry
        trajectory = trajectory_retry
        # Propagate retry-time island_topology and effective_image so post-hoc
        # forensics describe the final attempt, not the first one.
        if island_topology_retry:
            island_topology = island_topology_retry
        if effective_image_retry:
            effective_image = effective_image_retry

    return mut_output, user_input, run_log, island_topology, effective_image, trajectory, retry_info
