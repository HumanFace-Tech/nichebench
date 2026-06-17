"""Best-effort raw OpenCode session dump for timeout forensics.

This module provides a read-only snapshot of the OpenCode SQLite database
intended for post-hoc failure analysis when a run times out or is killed.
It captures:
  - Database schema (table list)
  - Latest session metadata
  - Up to 100 most recent messages with their raw data

Input source
------------
``opencode.db`` — the same SQLite database used for trajectory reconstruction.
This is a read-only diagnostic export; it does not modify DB state.

Failure modes
-------------
Returns ``None`` if the DB does not exist.  All other errors (lock, missing
tables, parse failures) are caught and result in a partial dump rather than
raising.

Ownership
--------
This is a diagnostics-only module.  It does NOT own trajectory building
(see ``sqlite``) or polling (see ``polling``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional


def dump_opencode_session_state(db_path: Path) -> Optional[Dict[str, Any]]:
    """Best-effort raw OpenCode session dump for timeout forensics."""
    if not db_path.exists():
        return None

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = sorted(row[0] for row in cur.fetchall())
        dump: Dict[str, Any] = {"db_path": str(db_path), "tables": tables}

        if "session" in tables and "message" in tables:
            cur.execute("SELECT id, time_created FROM session ORDER BY time_created DESC LIMIT 1")
            srow = cur.fetchone()
            if srow:
                session_id = srow[0]
                dump["latest_session"] = {"id": session_id, "time_created": srow[1]}
                cur.execute(
                    "SELECT id, data, time_created FROM message WHERE session_id = ? ORDER BY time_created ASC",
                    (session_id,),
                )
                messages = cur.fetchall()
                dump["message_count"] = len(messages)
                dump["messages"] = [{"id": row[0], "time_created": row[2], "data": row[1]} for row in messages[-100:]]
        elif "sessions" in tables and "messages" in tables:
            cur.execute("SELECT id, created_at FROM sessions ORDER BY created_at DESC LIMIT 1")
            srow = cur.fetchone()
            if srow:
                session_id = srow[0]
                dump["latest_session"] = {"id": session_id, "created_at": srow[1]}
                cur.execute(
                    (
                        "SELECT role, content, tool_calls, created_at "
                        "FROM messages WHERE session_id = ? ORDER BY created_at ASC"
                    ),
                    (session_id,),
                )
                rows = cur.fetchall()
                dump["message_count"] = len(rows)
                dump["messages"] = [
                    {
                        "role": row[0],
                        "content": row[1],
                        "tool_calls": row[2],
                        "created_at": row[3],
                    }
                    for row in rows[-100:]
                ]

        return dump
    finally:
        conn.close()
