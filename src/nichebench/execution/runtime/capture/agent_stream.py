"""OpenCode JSON event-stream parsing for capture.

Responsibilities
----------------
- Consume ``opencode run --format json --thinking`` lines.
- Journal every *part* of every message with its OpenCode identity, so a replay
  never has to reconstruct IDs.
- Keep **full tool inputs** (including large ``write``/``edit`` payloads) and
  **full tool results**. Nothing is truncated for the journal.
- Retain lines that are not the expected shape — non-JSON output and bare JSON
  arrays — instead of dropping them.
- Emit ``assistant_completed_text``: the text the scoring path uses, derived from
  assistant ``text`` parts only.

Event shapes actually emitted by the pinned runtime
---------------------------------------------------
``type`` is the discriminator. ``tool`` is the OpenCode part type; ``tool_use``
is the provider-level spelling that also appears in some streams. A tool part
carrying ``state`` holds both the call (``state.input``) and the result
(``state.output`` / ``state.error``), which is why the counters distinguish calls
from results instead of double-counting one part as both.

Ownership: parsing only. No subprocess, no proxy, no filesystem layout.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Set

TEXT_PART_TYPES = ("text",)
REASONING_PART_TYPES = ("reasoning", "thinking")
TOOL_PART_TYPES = ("tool", "tool-call", "tool_use")

# Envelope keys the pinned runtime uses when the part itself is nested.
_ENVELOPE_PART_KEYS = ("part", "parts", "content")


def _part_type(part: Dict[str, Any]) -> str:
    return str(part.get("type") or "")


def _part_text(part: Dict[str, Any]) -> str:
    for key in ("text", "content", "thinking", "reasoning"):
        value = part.get(key)
        if isinstance(value, str):
            return value
    return ""


def _tool_state(part: Dict[str, Any]) -> Dict[str, Any]:
    state = part.get("state")
    return state if isinstance(state, dict) else {}


def _tool_inputs(part: Dict[str, Any]) -> Dict[str, Any]:
    """Return the *full* tool input mapping for a tool part.

    Large ``write``/``edit`` payloads are returned verbatim: a capped tool input
    is not a faithful trace.
    """
    state = _tool_state(part)
    for source in (state, part):
        for key in ("input", "inputs", "arguments", "args"):
            value = source.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _tool_result(part: Dict[str, Any]) -> Any:
    state = _tool_state(part)
    if "output" in state:
        return state.get("output")
    if "error" in state:
        return {"error": state.get("error")}
    for key in ("output", "result"):
        if key in part:
            return part.get(key)
    return None


def _tool_status(part: Dict[str, Any]) -> Optional[str]:
    state = _tool_state(part)
    for key in ("status", "statusString"):
        value = state.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def derive_scoring_text(parts: Iterable[Dict[str, Any]]) -> str:
    """Reconstruct the assistant's final text from message parts.

    Only assistant ``text`` parts contribute. Reasoning, tool calls, tool
    results, step markers, and error objects are excluded, so the result is the
    same string the cage hands to scoring.
    """
    chunks: List[str] = []
    for part in parts:
        if _part_type(part) not in TEXT_PART_TYPES:
            continue
        text = _part_text(part)
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def _iter_events(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield each event object, unwrapping the envelope shapes in use.

    The pinned runtime emits three shapes: a flat part object, ``{"part": {...}}``,
    and ``{"parts": [...]}``.
    """
    raw_parts = payload.get("parts")
    if isinstance(raw_parts, list):
        for part in raw_parts:
            if isinstance(part, dict):
                yield part
        return
    for key in _ENVELOPE_PART_KEYS:
        nested = payload.get(key)
        if isinstance(nested, dict):
            yield nested
            return
    yield payload


def _retention_event(
    line_number: int,
    line: str,
    reason: str,
) -> Dict[str, Any]:
    """Build a record that keeps an unexpected stdout line instead of dropping it.

    ``reason`` is one of ``non_json``, ``json_non_object``, ``json_list``.
    Truncating the retained text is deliberate and recorded: the full bytes are
    already in the stdout spool, so the journal needs only enough to explain the
    shape.
    """
    return {
        "event": "agent_output_unparsed",
        "line": line_number,
        "reason": reason,
        "raw_preview": line[:2000],
        "raw_length": len(line),
        "full_bytes_location": "stdout.bin",
        "affects_completeness": True,
    }


def parse_opencode_json_stream(
    lines: Iterable[str],
    on_event: Any,
) -> Dict[str, Any]:
    """Parse the OpenCode JSON event stream and journal every observation.

    Args:
        lines: Raw stdout lines from ``opencode run --format json``.
        on_event: Callable invoked as ``on_event(record)`` for each record.

    Returns:
        Summary dict with ``messages``, ``assistant_text``, ``tool_calls``,
        ``tool_results``, ``reasoning_parts``, ``errors``, and the retention
        counters ``non_json_lines`` / ``json_list_lines`` / ``unparsed_lines``.
        ``parse_complete`` is False when any line could not be interpreted.
    """
    messages: List[Dict[str, Any]] = []
    assistant_text_chunks: List[str] = []
    tool_calls = 0
    tool_results = 0
    reasoning_parts = 0
    errors: List[str] = []
    non_json_lines = 0
    json_list_lines = 0
    unparsed = 0
    reported_call_ids: Set[str] = set()
    reported_result_ids: Set[str] = set()

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            non_json_lines += 1
            unparsed += 1
            on_event(_retention_event(line_number, line, "non_json"))
            continue
        if isinstance(parsed, list):
            # A bare JSON array carries no part identity; keep it verbatim.
            json_list_lines += 1
            unparsed += 1
            on_event(_retention_event(line_number, line, "json_list"))
            continue
        if not isinstance(parsed, dict):
            unparsed += 1
            on_event(_retention_event(line_number, line, "json_non_object"))
            continue

        message_id = str(parsed.get("messageID") or parsed.get("message_id") or "")
        session_id = str(parsed.get("sessionID") or parsed.get("session_id") or "")
        role = str(parsed.get("role") or "")
        for part in _iter_events(parsed):
            nested_message_id = str(part.get("messageID") or part.get("message_id") or message_id)
            nested_session_id = str(part.get("sessionID") or part.get("session_id") or session_id)
            part_id = str(part.get("id") or part.get("partID") or "")
            part_type = _part_type(part)
            base = {
                "line": line_number,
                "message_id": nested_message_id,
                "session_id": nested_session_id,
                "part_id": part_id,
                "role": str(part.get("role") or role),
                "part_type": part_type,
            }
            if part_type in TEXT_PART_TYPES:
                text = _part_text(part)
                on_event({**base, "event": "agent_text", "text": text})
                if base["role"] not in ("user", "system", "tool"):
                    assistant_text_chunks.append(text)
            elif part_type in REASONING_PART_TYPES:
                reasoning_parts += 1
                on_event({**base, "event": "agent_reasoning", "reasoning": _part_text(part)})
            elif part_type in TOOL_PART_TYPES:
                tool_name = part.get("tool") or part.get("name")
                call_id = str(part.get("callID") or part.get("call_id") or part_id or "")
                status = _tool_status(part)
                inputs = _tool_inputs(part)
                result = _tool_result(part)
                # A part with inputs *and* a completed state is one event, not
                # two: emit the call and the result, but count each once.
                key = call_id or f"{message_id}:{part_id}:{line_number}"
                has_input = any(
                    key in source
                    for source in (_tool_state(part), part)
                    for key in ("input", "inputs", "arguments", "args")
                )
                if has_input and key not in reported_call_ids:
                    reported_call_ids.add(key)
                    tool_calls += 1
                    on_event(
                        {
                            **base,
                            "event": "agent_tool_call",
                            "tool": tool_name,
                            "call_id": call_id,
                            "input": inputs,
                            "status": status,
                        }
                    )
                if result is not None and key not in reported_result_ids:
                    reported_result_ids.add(key)
                    tool_results += 1
                    on_event(
                        {
                            **base,
                            "event": "agent_tool_result",
                            "tool": tool_name,
                            "call_id": call_id,
                            "status": status,
                            "output": result,
                        }
                    )
                if not inputs and result is None:
                    # A tool part with neither: keep it rather than lose it.
                    on_event({**base, "event": "agent_tool_pending", "tool": tool_name, "call_id": call_id})
            elif part_type == "error":
                message = str(part.get("error") or part.get("message") or "")
                errors.append(message)
                on_event({**base, "event": "agent_error", "error": message})
            elif part_type in ("step-start", "step-finish"):
                on_event({**base, "event": f"agent_{part_type.replace('-', '_')}"})
            else:
                on_event({**base, "event": "agent_part", "part": part})
            messages.append({**base, "part_id": part_id})

    assistant_text = derive_scoring_text([{"type": "text", "text": chunk} for chunk in assistant_text_chunks])
    on_event(
        {
            "event": "assistant_completed_text",
            "text": assistant_text,
            "source_of_truth": "assistant text parts only (excludes reasoning and tool JSON)",
        }
    )
    return {
        "messages": messages,
        "assistant_text": assistant_text,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "reasoning_parts": reasoning_parts,
        "errors": errors,
        "non_json_lines": non_json_lines,
        "json_list_lines": json_list_lines,
        "unparsed_lines": unparsed,
        "parse_complete": unparsed == 0,
    }


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a single stdout line into a dict, or ``None`` when not a JSON object."""
    text = line.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
