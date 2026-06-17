"""SQLite polling for OpenCode watchdog conditions and watchdog marker resolution.

This module provides:
  - ``poll_opencode_db``: Watchdog polling against ``opencode.db`` to detect
    agent idle states and ``finish='stop'`` terminations
  - ``resolve_watchdog_marker``: Computes the appropriate watchdog trigger
    marker string from idle-time and threshold state

Input source
------------
``opencode.db`` — the same SQLite database used for trajectory reconstruction.
Polling is read-only with a 2-second timeout and does not modify DB state.

Failure modes
-------------
All functions are best-effort; DB lock, missing tables, or parse errors return
``None``/``False`` rather than raising.  Callers must handle the "no data yet"
case gracefully.

Ownership
--------
Does NOT build trajectories (see ``sqlite`` for that).  Does NOT own cage/OpenCode
lifecycle (see ``opencode_config``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional, Tuple


def resolve_watchdog_marker(
    has_stop: bool,
    idle_secs: float,
    stop_idle_seconds: float,
    inactivity_seconds: float,
) -> Optional[str]:
    """Compute the watchdog trigger marker from current idle/threshold state.

    When has_stop is True the stop-idle marker is only emitted once the agent
    has been idle for at least max(stop_idle_seconds, inactivity_seconds).
    This prevents a low stop_idle_seconds from killing a run earlier than the
    generic inactivity threshold would.

    When has_stop is False only the inactivity threshold applies. This keeps
    the two paths mutually exclusive: a stop-flow run is never reclassified as
    an inactivity event, and vice-versa.
    """
    if has_stop:
        if idle_secs >= max(stop_idle_seconds, inactivity_seconds):
            return "[WATCHDOG:stop-idle]"
        return None
    if idle_secs >= inactivity_seconds:
        return "[WATCHDOG:inactivity]"
    return None


def poll_opencode_db(db_path: Path) -> Tuple[Optional[str], bool]:
    """Poll the OpenCode SQLite DB for watchdog conditions.

    Returns:
        (latest_activity_marker, has_stop_finish)
        latest_activity_marker: opaque string for change-detection; None if no data yet.
        has_stop_finish: True when the latest assistant message has finish='stop'.
    """
    if not db_path.exists():
        return None, False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cur.fetchall()}

            latest_marker: Optional[str] = None
            has_stop = False

            if "session" in tables and "message" in tables:
                cur.execute("SELECT id FROM session ORDER BY time_created DESC LIMIT 1")
                srow = cur.fetchone()
                if not srow:
                    return None, False
                session_id = srow[0]

                # Latest activity from message table
                cur.execute(
                    "SELECT MAX(time_created) FROM message WHERE session_id = ?",
                    (session_id,),
                )
                mrow = cur.fetchone()
                msg_max = str(mrow[0]) if mrow and mrow[0] is not None else None

                # Latest activity from part table (if present)
                part_max: Optional[str] = None
                if "part" in tables:
                    cur.execute(
                        "SELECT MAX(p.time_created) FROM part p "
                        "JOIN message m ON p.message_id = m.id "
                        "WHERE m.session_id = ?",
                        (session_id,),
                    )
                    prow = cur.fetchone()
                    part_max = str(prow[0]) if prow and prow[0] is not None else None

                combined = f"{msg_max}|{part_max}"
                latest_marker = combined if (msg_max or part_max) else None

                # Check finish='stop' on most recent assistant message
                cur.execute(
                    "SELECT data FROM message WHERE session_id = ? ORDER BY time_created DESC LIMIT 10",
                    (session_id,),
                )
                for (data_str,) in cur.fetchall():
                    try:
                        data = json.loads(data_str) if isinstance(data_str, str) else {}
                        if data.get("role") == "assistant":
                            has_stop = data.get("finish") == "stop"
                            break
                    except Exception:
                        pass

            elif "sessions" in tables and "messages" in tables:
                # Legacy schema — no finish field, activity marker only
                cur.execute("SELECT id FROM sessions ORDER BY created_at DESC LIMIT 1")
                srow = cur.fetchone()
                if not srow:
                    return None, False
                session_id = srow[0]
                cur.execute(
                    "SELECT MAX(created_at) FROM messages WHERE session_id = ?",
                    (session_id,),
                )
                mrow = cur.fetchone()
                latest_marker = str(mrow[0]) if mrow and mrow[0] is not None else None

            return latest_marker, has_stop
        finally:
            conn.close()
    except Exception:
        return None, False
