"""Heuristic catastrophic failure detection for runtime artifacts.

This module only classifies harness-level failure signals from logs and
trajectory metadata. It does not decide scoring or persist outputs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


def detect_catastrophic_failure(
    run_log: Optional[str],
    trajectory: Optional[Dict[str, Any]],
    mut_output: Optional[str],
) -> Optional[str]:
    """Classify catastrophic harness-level failures from run.log and trajectory.

    Returns a human-readable reason string if a catastrophic failure is detected,
    or None if execution appears nominally normal.
    """
    if "[WATCHDOG:stop-idle]" in (run_log or ""):
        return "Watchdog stop-idle: agent did not exit cleanly"
    if "[WATCHDOG:inactivity]" in (mut_output or ""):
        return f"Watchdog inactivity: agent execution stalled: {(mut_output or '')[:200]}"
    if "[WATCHDOG:inactivity]" in (run_log or ""):
        return f"Watchdog inactivity: agent execution stalled: {(run_log or '')[:200]}"
    if "[Error:" in (run_log or "") and "timed out" in (run_log or "").lower():
        return f"Agent execution timed out: {(run_log or '')[:200]}"
    if "[Error:" in (mut_output or "") and "timed out" in (mut_output or "").lower():
        return f"Agent execution timed out: {(mut_output or '')[:200]}"
    if "dh is not a function" in ((run_log or "").lower()):
        return "Fatal startup failure: 'dh is not a function' detected in run.log"
    has_tool_activity = bool(
        isinstance(trajectory, dict)
        and trajectory
        and any(msg.get("tool_calls") for msg in trajectory.get("messages", []))
    ) or bool((run_log or "") and ("$ " in (run_log or "") or "→ " in (run_log or "")))
    stderr_error_match = re.search(r"STDERR:\s*Error:", run_log or "", re.IGNORECASE)
    if stderr_error_match and not has_tool_activity:
        start = stderr_error_match.start()
        snippet = (run_log or "")[start : start + 200]
        return f"Agent startup error with no tool activity: {snippet}"
    return None
