"""OpenCode session directory discovery and JSON-based trajectory building.

This module handles:
  - Locating the OpenCode sessions directory (XDG_DATA_HOME or ``~/.local/share``)
  - Enumerating session IDs and selecting sessions by age/newest
  - Building a trajectory from a session directory containing JSON message files

Input sources
-------------
Primary: OpenCode session directory with ``*.json`` message files (one per turn).
The directory may be under the run-scoped ``XDG_DATA_HOME/opencode/storage/message``
or the global ``~/.local/share/opencode/storage/message``.

Failure modes
------------
All functions are best-effort and return empty/None rather than raising on:
  - Missing directories
  - Permission errors
  - Malformed JSON in session files

Ownership
--------
Does NOT poll SQLite (see ``polling``) or parse SQLite-backed trajectories (see
``sqlite``).  Does NOT own cage/OpenCode lifecycle (see ``opencode_config``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

from nichebench.execution.runtime.trajectory.normalise import normalise_message


def opencode_sessions_dir(xdg_data_home: Optional[Path] = None) -> Optional[Path]:
    """Find OpenCode sessions directory.

    Args:
        xdg_data_home: Optional XDG_DATA_HOME path for run-scoped storage

    Returns:
        Path to sessions directory, or None if not found
    """
    if xdg_data_home is not None:
        # Use run-scoped XDG_DATA_HOME first (Fix 1)
        opencode_base = xdg_data_home / "opencode" / "storage"
        message_dir = opencode_base / "message"
        if message_dir.exists():
            return message_dir
        session_dir = opencode_base / "session"
        if session_dir.exists():
            return session_dir
        return None

    # Fall back to global ~/.local/share path
    try:
        base = Path.home() / ".local" / "share" / "opencode" / "storage"
        message_dir = base / "message"
        if message_dir.exists():
            return message_dir
        session_dir = base / "session"
        if session_dir.exists():
            return session_dir
    except Exception:
        pass
    return None


def snapshot_session_ids(sessions_dir: Optional[Path]) -> Set[str]:
    """Get set of session IDs from sessions directory.

    Args:
        sessions_dir: Path to sessions directory

    Returns:
        Set of session IDs (directory names)
    """
    if not sessions_dir or not sessions_dir.exists():
        return set()
    try:
        return {d.name for d in sessions_dir.iterdir() if d.is_dir()}
    except Exception:
        return set()


def pick_newest_session(sessions_dir: Path, session_ids: Set[str]) -> Optional[Path]:
    """Pick newest session by directory modification time from given set.

    Args:
        sessions_dir: Path to sessions directory
        session_ids: Set of session IDs to consider

    Returns:
        Newest session Path, or None if not found
    """
    if not sessions_dir or not sessions_dir.exists() or not session_ids:
        return None
    try:
        sessions = [(d, d.stat().st_mtime) for d in sessions_dir.iterdir() if d.is_dir() and d.name in session_ids]
        if not sessions:
            return None
        return sorted(sessions, key=lambda x: x[1], reverse=True)[0][0]
    except Exception:
        return None


def pick_session_by_mtime(sessions_dir: Path, window_start: datetime, window_end: datetime) -> Optional[Path]:
    """Pick session modified within time window.

    Args:
        sessions_dir: Path to sessions directory
        window_start: Start of time window
        window_end: End of time window

    Returns:
        Session Path if modified within window, or None if not found
    """
    if not sessions_dir or not sessions_dir.exists():
        return None
    try:
        for d in sessions_dir.iterdir():
            if d.is_dir():
                mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
                if window_start <= mtime <= window_end:
                    return d
        return None
    except Exception:
        return None


def build_trajectory(
    session_dir: Path,
    test_case_id: str,
    model_str: str,
    start_time: datetime,
    end_time: datetime,
) -> Dict[str, Any]:
    """Build trajectory from OpenCode session directory.

    Args:
        session_dir: Path to session directory
        test_case_id: Test case ID
        model_str: Model string
        start_time: Run start time
        end_time: Run end time

    Returns:
        Trajectory dict with messages and stats
    """
    messages = []
    input_tokens = 0
    output_tokens = 0

    if session_dir and session_dir.exists():
        # Read all JSON files in session directory
        try:
            for msg_file in sorted(session_dir.glob("*.json")):
                try:
                    raw = json.loads(msg_file.read_text(encoding="utf-8"))
                    msg = normalise_message(raw)
                    messages.append(msg)

                    # Extract token counts from usage field (Fix 3)
                    usage = raw.get("usage", {})
                    if isinstance(usage, dict):
                        try:
                            input_tokens += int(usage.get("input_tokens", 0))
                            output_tokens += int(usage.get("output_tokens", 0))
                        except (ValueError, TypeError):
                            # Non-numeric token values are silently ignored (Fix 3)
                            pass
                except (json.JSONDecodeError, Exception):
                    # Skip malformed JSON files
                    pass
        except Exception:
            pass

    total_turns = len(messages)

    return {
        "instance_id": test_case_id,
        "model": model_str,
        "created_at": start_time.isoformat(),
        "ended_at": end_time.isoformat(),
        "messages": messages,
        "stats": {
            "total_turns": total_turns,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_seconds": (end_time - start_time).total_seconds(),
        },
    }
