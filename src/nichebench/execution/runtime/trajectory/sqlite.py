"""SQLite-backed trajectory reconstruction from OpenCode's opencode.db.

This module reads the OpenCode SQLite database to build a complete trajectory
including message parts (thinking/reasoning/text), token accounting from both
``usage`` and ``tokens`` fields, and support for both the current schema
(``session`` / ``message`` / ``part``) and the legacy schema
(``sessions`` / ``messages``).

Input source
------------
``opencode.db`` — SQLite database created by the OpenCode runtime.
Two schemas are supported:
  - Current: ``session``, ``message``, ``part`` tables (part.type is inside JSON)
  - Legacy: ``sessions``, ``messages`` tables (tool_calls stored as JSON string)

Failure modes
-------------
All functions are best-effort; missing tables, empty DB, or parse errors return
``None`` rather than raising.  Callers must check whether the returned
trajectory is usable.

Ownership
--------
Does NOT discover session directories (see ``session_files``).  Does NOT own
cage/OpenCode lifecycle (see ``opencode_config``).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def build_trajectory_from_sqlite(
    db_path: Path,
    test_case_id: str,
    model_str: str,
    start_time: datetime,
    end_time: datetime,
    system_prompt: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build trajectory payload from OpenCode SQLite database.

    Args:
        db_path: Path to opencode.db file
        test_case_id: Test case ID for metadata
        model_str: Model string used for the run
        start_time: Run start time
        end_time: Run end time
        system_prompt: System prompt used (optional)

    Returns:
        Trajectory dict with messages and stats, or None if DB not found/invalid
    """
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if sessions table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='session'")
        if not cursor.fetchone():
            # Primary schema not found — attempt legacy schema fallback (sessions, plural)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
            if not cursor.fetchone():
                conn.close()
                return None

            # Legacy schema: sessions/messages tables with plain columns
            cursor.execute("SELECT id FROM sessions ORDER BY created_at DESC")
            legacy_sessions = cursor.fetchall()
            if not legacy_sessions:
                conn.close()
                return None
            legacy_session_id = legacy_sessions[0][0]
            cursor.execute(
                "SELECT role, content, tool_calls, created_at FROM messages "
                "WHERE session_id = ? ORDER BY created_at ASC",
                (legacy_session_id,),
            )
            legacy_rows = cursor.fetchall()
            conn.close()

            legacy_messages: List[Dict[str, Any]] = []
            for legacy_row in legacy_rows:
                legacy_role, legacy_content, legacy_tool_calls_json, legacy_created_at = legacy_row
                legacy_msg: Dict[str, Any] = {
                    "role": legacy_role,
                    "content": legacy_content,
                    "created_at": legacy_created_at,
                }
                if legacy_tool_calls_json:
                    with contextlib.suppress(Exception):
                        legacy_msg["tool_calls"] = json.loads(legacy_tool_calls_json)
                legacy_messages.append(legacy_msg)

            legacy_trajectory: Dict[str, Any] = {
                "instance_id": test_case_id,
                "model": model_str,
                "created_at": start_time.isoformat(),
                "ended_at": end_time.isoformat(),
                "messages": legacy_messages,
                "stats": {
                    "total_turns": len([m for m in legacy_messages if m["role"] == "assistant"]),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "duration_seconds": (end_time - start_time).total_seconds(),
                },
            }
            if system_prompt:
                legacy_trajectory["system_prompt"] = system_prompt
            return legacy_trajectory

        # Get all sessions
        cursor.execute("SELECT id FROM session ORDER BY time_created DESC")
        sessions = cursor.fetchall()

        if not sessions:
            return None

        # Get the newest session
        session_id = sessions[0][0]

        # Get messages for this session
        cursor.execute(
            "SELECT id, session_id, data, time_created FROM message " "WHERE session_id = ? ORDER BY time_created ASC",
            (session_id,),
        )
        message_rows = cursor.fetchall()

        # Get parts for messages (support both real and legacy schemas)
        cursor.execute("PRAGMA table_info(part)")
        part_columns = {row[1] for row in cursor.fetchall()}
        has_legacy_type_column = "type" in part_columns
        if has_legacy_type_column:
            cursor.execute(
                "SELECT id, message_id, type, data, time_created FROM part "
                "WHERE message_id IN (SELECT id FROM message WHERE session_id = ?) "
                "ORDER BY time_created ASC",
                (session_id,),
            )
        else:
            cursor.execute(
                "SELECT id, message_id, data, time_created FROM part "
                "WHERE message_id IN (SELECT id FROM message WHERE session_id = ?) "
                "ORDER BY time_created ASC",
                (session_id,),
            )
        part_rows = cursor.fetchall()

        conn.close()

        # Build messages map
        messages_map: Dict[str, Dict[str, Any]] = {}
        trajectory_input_tokens = 0
        trajectory_output_tokens = 0
        for msg_id, msg_session_id, msg_data, msg_time in message_rows:
            try:
                msg_json = json.loads(msg_data)
                usage = msg_json.get("usage", {})
                if isinstance(usage, dict):
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    if isinstance(input_tokens, (int, float)):
                        trajectory_input_tokens += int(input_tokens)
                    if isinstance(output_tokens, (int, float)):
                        trajectory_output_tokens += int(output_tokens)

                tokens = msg_json.get("tokens", {})
                if isinstance(tokens, dict):
                    input_tokens = tokens.get("input", 0)
                    output_tokens = tokens.get("output", 0)
                    if isinstance(input_tokens, (int, float)):
                        trajectory_input_tokens += int(input_tokens)
                    if isinstance(output_tokens, (int, float)):
                        trajectory_output_tokens += int(output_tokens)

                # Check if message has content field
                has_content = "content" in msg_json and msg_json["content"]
                messages_map[msg_id] = {
                    "role": msg_json.get("role", "unknown"),
                    "content": msg_json.get("content", ""),
                    "created_at": msg_time,
                    "tool_calls": msg_json.get("tool_calls"),
                    "tool_call_id": msg_json.get("tool_call_id"),
                    "_has_content": has_content,  # Track if original had content
                    "_text_parts": [],  # Collect text parts for rebuilding
                    "_thinking_parts": [],  # Collect thinking parts
                    "_all_parts": [],  # Collect all parts
                }
            except Exception:
                pass

        # Merge parts into messages (thinking/reasoning/text)
        for part_row in part_rows:
            if has_legacy_type_column:
                part_id, msg_id, legacy_part_type, part_data, part_time = part_row
            else:
                part_id, msg_id, part_data, part_time = part_row
                legacy_part_type = ""
            if msg_id in messages_map:
                try:
                    part_json = json.loads(part_data)
                    if not isinstance(part_json, dict):
                        part_json = {"text": str(part_json)}
                    part_type = str(legacy_part_type or part_json.get("type", ""))
                    if part_type and "type" not in part_json:
                        part_json["type"] = part_type
                    # Store all parts
                    messages_map[msg_id]["_all_parts"].append(part_json)

                    if part_type in ("thinking", "reasoning"):
                        # Collect thinking parts
                        thinking_text = part_json.get("text") or part_json.get("data", "")
                        if thinking_text:
                            messages_map[msg_id]["_thinking_parts"].append({"text": thinking_text})
                    elif part_type == "text":
                        # Collect text parts for rebuilding if original had no content
                        if not messages_map[msg_id].get("_has_content", False):
                            messages_map[msg_id]["_text_parts"].append(part_json.get("text", ""))
                except Exception:
                    pass

        # Rebuild content from text parts if needed and finalize messages
        for msg_id in messages_map:
            # Rebuild content from text parts if original had no content
            if not messages_map[msg_id].get("_has_content", False) and messages_map[msg_id].get("_text_parts"):
                messages_map[msg_id]["content"] = "\n".join(messages_map[msg_id]["_text_parts"])

            # Add thinking parts if any
            if messages_map[msg_id].get("_thinking_parts"):
                messages_map[msg_id]["thinking"] = messages_map[msg_id]["_thinking_parts"]

            # Add all parts if any
            if messages_map[msg_id].get("_all_parts"):
                messages_map[msg_id]["parts"] = messages_map[msg_id]["_all_parts"]

            # Remove internal tracking fields
            messages_map[msg_id].pop("_has_content", None)
            messages_map[msg_id].pop("_text_parts", None)
            messages_map[msg_id].pop("_thinking_parts", None)
            messages_map[msg_id].pop("_all_parts", None)

        # Build messages list in order
        messages = []
        for msg_id, _, _, _ in message_rows:
            if msg_id in messages_map:
                msg = messages_map[msg_id]
                # Clean up tool_calls if None
                if msg.get("tool_calls") is None:
                    msg.pop("tool_calls", None)
                if msg.get("tool_call_id") is None:
                    msg.pop("tool_call_id", None)
                messages.append(msg)

        trajectory = {
            "instance_id": test_case_id,
            "model": model_str,
            "created_at": start_time.isoformat(),
            "ended_at": end_time.isoformat(),
            "messages": messages,
            "stats": {
                "total_turns": len(messages),
                "input_tokens": trajectory_input_tokens,
                "output_tokens": trajectory_output_tokens,
                "duration_seconds": (end_time - start_time).total_seconds(),
            },
        }

        if system_prompt:
            trajectory["system_prompt"] = system_prompt

        return trajectory

    except Exception:
        return None
