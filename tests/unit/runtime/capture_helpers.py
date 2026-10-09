"""Shared helpers for runtime capture tests.

Everything here is offline: no network, no Docker, no paid provider calls.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from nichebench.execution.runtime.capture import CaptureRun, CaptureSession, CaptureSettings


def make_settings(**overrides: Any) -> CaptureSettings:
    """Build enabled capture settings for the supported route."""
    base: Dict[str, Any] = {
        "enabled": True,
        "quota_bytes": 4 * 1024 * 1024 * 1024,
        "retention": "standard",
        "route": "minimax_m3",
    }
    base.update(overrides)
    return CaptureSettings(**base)


def make_session(
    tmp_path: Path,
    known_credentials: Optional[List[str]] = None,
    **overrides: Any,
) -> CaptureSession:
    """Allocate a capture session under ``tmp_path``."""
    run = CaptureRun.allocate(tmp_path, make_settings(**overrides), "test_001")
    return CaptureSession(run, known_credentials=known_credentials or [])


def events(attempt: Path) -> List[Dict[str, Any]]:
    """Read every journal record from an attempt directory."""
    path = attempt / "events.jsonl"
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def events_of(records: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    """Filter journal records by event name."""
    return [r for r in records if r.get("event") == name]


def write_opencode_db(
    data_dir: Path,
    sessions: int = 1,
    messages: int = 3,
    parts: int = 2,
    wal_only_row: bool = False,
    keep_open: bool = False,
) -> Path:
    """Create a minimal OpenCode-shaped SQLite store under ``data_dir``.

    Mirrors the current schema (``session`` / ``message`` / ``part``) with the
    part type carried inside the row JSON, which is what the pinned runtime
    writes.
    """
    db_dir = data_dir / "opencode"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "opencode.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, time_created INTEGER)")
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created INTEGER)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created INTEGER)")
    for s in range(sessions):
        session_id = f"ses_{s:03d}"
        conn.execute("INSERT INTO session VALUES (?, ?)", (session_id, 1000 + s))
        for m in range(messages):
            message_id = f"{session_id}_msg_{m:03d}"
            role = "user" if m == 0 else "assistant"
            conn.execute(
                "INSERT INTO message VALUES (?, ?, ?, ?)",
                (
                    message_id,
                    session_id,
                    json.dumps({"role": role, "finish": "stop", "usage": {"input_tokens": 10, "output_tokens": 5}}),
                    1100 + s * 100 + m,
                ),
            )
            for p in range(parts):
                conn.execute(
                    "INSERT INTO part VALUES (?, ?, ?, ?)",
                    (
                        f"{message_id}_part_{p:03d}",
                        message_id,
                        json.dumps({"type": "text", "text": f"chunk {m}.{p}"}),
                        1100 + s * 100 + m * 10 + p,
                    ),
                )
    conn.commit()
    if wal_only_row:
        # Written after the main commit so the WAL still holds uncheckpointed
        # frames; the backup path must pick this row up.
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("ses_000_msg_wal", "ses_000", json.dumps({"role": "assistant", "finish": "stop"}), 9999),
        )
        conn.commit()
    if keep_open:
        # Leave the writer connected so the WAL sidecar survives; callers must
        # close it themselves.
        global _OPEN_CONNECTIONS
        _OPEN_CONNECTIONS.append(conn)
    else:
        conn.close()
    return db_path


_OPEN_CONNECTIONS: list = []


def close_open_databases() -> None:
    """Close any connection left open by ``write_opencode_db(keep_open=True)``."""
    while _OPEN_CONNECTIONS:
        _OPEN_CONNECTIONS.pop().close()


def jsonl_line(payload: Dict[str, Any]) -> str:
    """Serialise a dict as one JSONL line."""
    return json.dumps(payload)
