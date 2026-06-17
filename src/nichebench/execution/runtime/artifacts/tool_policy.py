"""Runtime tool policy helpers.

This module owns extraction and validation of tool usage signals from runtime
trajectories and run logs. It does not persist artifacts or classify runtime
failures.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set


def extract_trajectory_tool_names(trajectory: Dict[str, Any]) -> Set[str]:
    """Extract normalized tool names used in trajectory messages."""
    used_tools: Set[str] = set()
    messages = trajectory.get("messages")
    if not isinstance(messages, list):
        return used_tools

    for message in messages:
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue

        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            tool_name: Optional[str] = None
            if isinstance(function, dict):
                name = function.get("name")
                if name:
                    tool_name = str(name)
            elif isinstance(function, str):
                tool_name = function
            elif tool_call.get("name"):
                tool_name = str(tool_call.get("name"))

            if tool_name:
                used_tools.add(tool_name.strip().lower())

    return used_tools


def parse_rejected_tool_attempts(run_log: str) -> List[Dict[str, str]]:
    """Parse rejected tool attempts from run.log output.

    Handles two error patterns:
      1. "attempted to call tool 'X' which was not in request.tools"
         when a tool is called that was not included in the request's tools list.
      2. "parameters for tool X did not match schema: missing properties: 'Y'"
         when a tool is called with invalid/missing parameters.

    Args:
        run_log: The raw run.log text from the cage container.

    Returns:
        List of dicts with ``tool_name`` and ``error_message`` keys.
    """
    rejected: List[Dict[str, str]] = []

    if not run_log:
        return rejected

    # Pattern 1: "attempted to call tool 'TOOL_NAME' which was not in request.tools"
    # Also handles variants like: "attempted to call tool 'TOOL_NAME' ..."
    pattern_rejected = re.compile(
        r"attempted to call tool ['\"]([^'\"]+)['\"](.*?)(?:\n|$)",
        re.IGNORECASE,
    )
    for match in pattern_rejected.finditer(run_log):
        tool_name = match.group(1).strip().lower()
        error_extra = match.group(2).strip()
        rejected.append(
            {
                "tool_name": tool_name,
                "error_message": (
                    f"attempted to call tool '{tool_name}' which was not in request.tools {error_extra}"
                ).strip(),
            }
        )

    # Pattern 2: "parameters for tool TOOL_NAME did not match schema: ..."
    # Example: "parameters for tool read did not match schema: missing properties: 'filePath'"  # noqa: ERA001
    pattern_schema = re.compile(
        r"parameters for tool ['\"]?([^'\"\n]+?)['\"]? did not match schema[:\s]+([^\n]*)",
        re.IGNORECASE,
    )
    for match in pattern_schema.finditer(run_log):
        tool_name = match.group(1).strip().lower()
        error_detail = match.group(2).strip()
        rejected.append(
            {
                "tool_name": tool_name,
                "error_message": f"parameters for tool '{tool_name}' did not match schema: {error_detail}".strip(),
            }
        )

    return rejected


def build_tool_allowlist_check(
    trajectory: Optional[Dict[str, Any]],
    rejected_tool_attempts: Optional[List[Dict[str, str]]] = None,
    enforce: bool = False,
) -> Optional[Any]:
    """Build deterministic tool allowlist check from trajectory and rejected attempts.

    Args:
        trajectory: Trajectory dict from OpenCode session
        rejected_tool_attempts: Optional list of rejected attempt dicts with 'tool_name' keys
        enforce: If True, check fails hard on disallowed tools. If False (default),
                 check passes but records disallowed/rejected attempts in details.

    Returns:
        CheckResult if trajectory is present, None otherwise
    """
    if not trajectory and not rejected_tool_attempts:
        return None

    # Import here to avoid circular imports at module load time.
    from nichebench.execution.runtime.scoring import CheckResult

    used_tools = extract_trajectory_tool_names(trajectory) if trajectory else set()

    # Include rejected tool attempts in the union of used/disallowed tools
    if rejected_tool_attempts:
        rejected_tool_names = {
            attempt["tool_name"].strip().lower() for attempt in rejected_tool_attempts if attempt.get("tool_name")
        }
        used_tools = used_tools | rejected_tool_names

    allowed_tools = {"bash", "read", "write", "edit"}
    disallowed_tools = sorted(tool for tool in used_tools if tool not in allowed_tools)
    passed = not disallowed_tools if enforce else True

    return CheckResult(
        name="tool_allowlist",
        type="tool_policy",
        passed=passed,
        message=f"Allowed tools: {', '.join(sorted(used_tools))}"
        if passed
        else f"Disallowed tools: {', '.join(disallowed_tools)}",
        is_critical=False,
        details={"disallowed_tools": disallowed_tools, "rejected_tool_attempts": rejected_tool_attempts or []},
    )
