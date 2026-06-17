"""Tool and message normalisation for OpenCode session files.

This module normalises raw message dicts from OpenCode session storage into a
consistent internal format.  It handles:
  - List-based content (e.g. ``[{"type": "text", "text": "..."}]``) → flattened string
  - Tool calls and tool_call_id passthrough
  - Missing fields defaulting gracefully

Ownership
--------
Input: raw JSON from OpenCode session ``*.json`` files or SQLite ``message.data``.
Output: normalised ``message`` dict suitable for trajectory assembly.
Does NOT own SQLite polling or session discovery (see ``sqlite`` and ``session_files``).
"""

from __future__ import annotations

import contextlib
from typing import Any, Dict


def normalise_message(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a message from OpenCode storage.

    Args:
        raw: Raw message dict from storage

    Returns:
        Normalized message dict
    """
    msg: Dict[str, Any] = {
        "role": str(raw.get("role", "unknown")),
        "content": "",
    }

    # Handle content as string or list
    content = raw.get("content", "")
    if isinstance(content, list):
        # Join text fields from list format
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                text_parts.append(str(item.get("text", "")))
            else:
                text_parts.append(str(item))
        msg["content"] = "".join(text_parts)
    else:
        msg["content"] = str(content)

    if "tool_calls" in raw:
        tool_calls = raw["tool_calls"]
        if tool_calls:
            with contextlib.suppress(Exception):
                msg["tool_calls"] = tool_calls if isinstance(tool_calls, list) else [tool_calls]
    if "tool_call_id" in raw:
        msg["tool_call_id"] = str(raw["tool_call_id"])
    return msg
