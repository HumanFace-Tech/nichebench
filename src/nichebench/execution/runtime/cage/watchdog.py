"""Inactivity/stop-idle watchdog helpers for cage container execution.

**Ownership**: This module is owned by ``CageExecutionMixin`` (mixin.py). It
contains watchdog-related helpers that are currently unused by the main mixin
but available for future extraction.

**Container safety constraints**:
- Watchdog thresholds are configurable via runtime config.
- The watchdog only reads the OpenCode SQLite DB; it never modifies state.
- Watchdog termination is always followed by container cleanup.
"""

from __future__ import annotations

import time
from typing import Callable, Optional, Tuple


def resolve_watchdog_trigger(
    db_marker: Optional[str],
    last_db_marker: Optional[str],
    last_activity_mono: float,
    has_stop: bool,
    stop_idle_seconds: float,
    inactivity_seconds: float,
    resolve_watchdog_marker_fn: Callable[[bool, float, float, float], Optional[str]],
) -> Tuple[Optional[str], float, Optional[str]]:
    """Resolve watchdog state and compute trigger marker if applicable.

    Args:
        db_marker: Current DB marker from polling.
        last_db_marker: Previous DB marker.
        last_activity_mono: Monotonic time of last activity.
        has_stop: Whether agent sent a stop tool call.
        stop_idle_seconds: Idle threshold in seconds.
        inactivity_seconds: Inactivity threshold in seconds.
        resolve_watchdog_marker_fn: Function to resolve watchdog marker.

    Returns:
        Tuple of (new_last_db_marker, new_last_activity_mono, watchdog_marker).
    """
    new_last_db_marker = last_db_marker
    new_last_activity_mono = last_activity_mono
    watchdog_marker: Optional[str] = None

    if db_marker is not None and db_marker != last_db_marker:
        new_last_db_marker = db_marker
        new_last_activity_mono = time.monotonic()

    idle_secs = time.monotonic() - new_last_activity_mono
    watchdog_marker = resolve_watchdog_marker_fn(has_stop, idle_secs, stop_idle_seconds, inactivity_seconds)

    return new_last_db_marker, new_last_activity_mono, watchdog_marker
