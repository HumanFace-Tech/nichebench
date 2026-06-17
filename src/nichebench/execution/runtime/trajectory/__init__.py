"""OpenCode trajectory capture and reconstruction helpers.

Extracts SQLite polling, session discovery, trajectory building, and watchdog
resolution from the orchestrator.

This is a package re-exporting the following public API:

===================  ==============================================================
Module               Symbols
===================  ==============================================================
``normalise``        ``normalise_message``
``session_files``    ``opencode_sessions_dir``, ``snapshot_session_ids``,
                     ``pick_newest_session``, ``pick_session_by_mtime``,
                     ``build_trajectory``
``sqlite``           ``build_trajectory_from_sqlite``
``polling``          ``poll_opencode_db``, ``resolve_watchdog_marker``
``debug_dump``       ``dump_opencode_session_state``
===================  ==============================================================

Trajectory reconstruction inputs/outputs
----------------------------------------
Primary input: OpenCode session directory (JSON message files) or ``opencode.db``.
Fallback input: legacy session SQLite schema.

Outputs: a ``trajectory`` dict with keys:
  - ``instance_id``: test case identifier
  - ``model``: model string used
  - ``created_at`` / ``ended_at``: ISO timestamps
  - ``messages``: list of normalized messages (role, content, tool_calls, etc.)
  - ``stats``: total_turns, input_tokens, output_tokens, duration_seconds
  - ``system_prompt``: injected system prompt (when provided)

Failure tolerance
-----------------
All reconstruction functions are best-effort; malformed JSON or missing files
return empty/partial results rather than raising.  Callers must check whether
the returned trajectory is usable for their purpose.

Ownership boundaries
--------------------
- Does NOT capture or write artifacts (see ``artifacts``)
- Does NOT own cage/OpenCode lifecycle (see ``opencode_config``)
- Does NOT own workspace/DDEV lifecycle (see ``workspace``)
"""

from __future__ import annotations

from nichebench.execution.runtime.trajectory.debug_dump import (
    dump_opencode_session_state,
)
from nichebench.execution.runtime.trajectory.normalise import normalise_message
from nichebench.execution.runtime.trajectory.polling import (
    poll_opencode_db,
    resolve_watchdog_marker,
)
from nichebench.execution.runtime.trajectory.session_files import (
    build_trajectory,
    opencode_sessions_dir,
    pick_newest_session,
    pick_session_by_mtime,
    snapshot_session_ids,
)
from nichebench.execution.runtime.trajectory.sqlite import build_trajectory_from_sqlite

__all__ = [
    # normalise
    "normalise_message",
    # session_files
    "opencode_sessions_dir",
    "snapshot_session_ids",
    "pick_newest_session",
    "pick_session_by_mtime",
    "build_trajectory",
    # sqlite
    "build_trajectory_from_sqlite",
    # polling
    "poll_opencode_db",
    "resolve_watchdog_marker",
    # debug_dump
    "dump_opencode_session_state",
]
