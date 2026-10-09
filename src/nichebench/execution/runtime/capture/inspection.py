"""Read-only capture inspection. Reconstruct bodies, not individual blob JSON.

HTTP attempts are identified by request ID, not by logical model turn. Partial
SSE tails are retained as fragments, never counted as malformed complete events.
Nothing here invents bytes or reads beyond this archive's observation boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from nichebench.execution.runtime.capture.store import BLOCK_SIZE

DESCRIPTOR_VERSION = 2


def read_payload(attempt_path: Path, ref: Mapping[str, Any]) -> bytes:
    """Resolve a payload reference with hash, range, and manifest verification."""

    def block(entry: Mapping[str, Any]) -> bytes:
        digest = str(entry["sha256"])
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid capture blob digest")
        raw = (attempt_path / "blobs" / f"{digest}.bin").read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("capture blob hash mismatch")
        offset, length = int(entry["offset"]), int(entry["length"])
        if offset < 0 or length < 0 or offset + length > len(raw):
            raise ValueError("capture blob range out of bounds")
        return raw[offset : offset + length]

    raw = block(ref)
    try:
        manifest = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw
    if not isinstance(manifest, dict) or manifest.get("block_size") != BLOCK_SIZE or "blocks" not in manifest:
        return raw
    if not isinstance(manifest["blocks"], list):
        raise ValueError("invalid capture block manifest")
    payload = b"".join(block(entry) for entry in manifest["blocks"])
    if len(payload) != manifest.get("size"):
        raise ValueError("capture manifest length mismatch")
    return payload


@dataclass(frozen=True)
class ResponseCapture:
    request_id: str
    body: bytes
    state: str
    exact: bool
    bytes_received: Optional[int]
    bytes_forwarded: Optional[int]
    problems: tuple[str, ...]


def reconstruct_response(
    attempt_path: Path,
    records: Sequence[Mapping[str, Any]],
    request_id: str,
) -> ResponseCapture:
    """Join each stored response chunk exactly once in stored-offset order.

    These offsets describe the stored copy, which can differ from received bytes
    after credential redaction. A missing range is an error, not padding. Terminal
    metadata is authoritative for received/forwarded counts; older interruption
    records incorrectly labelled received bytes as forwarded.
    """
    selected = [r for r in records if str(r.get("request_id", "")) == request_id]
    chunks = sorted(
        [r for r in selected if r.get("payload_name") == "response_body"],
        key=lambda r: (int(r["offset"]), int(r.get("seq", 0))),
    )
    body = bytearray()
    for chunk in chunks:
        if int(chunk["offset"]) != len(body):
            raise ValueError("capture response chunk gap or overlap")
        payload = read_payload(attempt_path, chunk["payload_ref"])
        if len(payload) != int(chunk["byte_length"]):
            raise ValueError("capture response chunk length mismatch")
        body.extend(payload)
    terminals = [r for r in selected if r.get("event") == "http_response_complete"]
    terminal = terminals[-1] if terminals else {}
    problems: List[str] = []
    if len(terminals) != 1:
        problems.append("response_terminal_missing_or_duplicate")
    state = str(terminal.get("state", "unfinalised"))
    if state != "complete":
        problems.append("stream_incomplete")
    if terminal.get("stored_byte_length") is not None and len(body) != terminal["stored_byte_length"]:
        problems.append("stored_length_mismatch")
    received = terminal.get("bytes_received")
    exclusions = terminal.get("exclusions", [])
    if received is not None and "known_credential_replaced" not in exclusions and len(body) != received:
        problems.append("observed_bytes_not_retained")
    return ResponseCapture(
        request_id,
        bytes(body),
        state,
        bool(terminal.get("exact")) and not problems,
        int(received) if received is not None else None,
        int(terminal["bytes_forwarded"]) if "bytes_forwarded" in terminal else None,
        tuple(problems),
    )


@dataclass(frozen=True)
class SSEFrame:
    data: bytes
    terminated: bool


def split_sse_frames(body: bytes) -> List[SSEFrame]:
    """Parse SSE framing after byte reconstruction, including an unfinished tail.

    Multiline data fields form one event. Comment/other fields are ignored. A
    trailing fragment without a blank-line delimiter is not a complete event,
    even when its data happens to be valid JSON.
    """
    normalised = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    frames: List[SSEFrame] = []
    data: List[bytes] = []
    lines = normalised.split(b"\n")
    if normalised.endswith(b"\n"):
        lines.pop()  # EOF's synthetic empty element is not a blank SSE line.
    for line in lines:
        if not line:
            if data:
                frames.append(SSEFrame(b"\n".join(data), True))
                data = []
            continue
        field, _, value = line.partition(b":")
        if field == b"data":
            data.append(value[1:] if value.startswith(b" ") else value)
    if data:
        frames.append(SSEFrame(b"\n".join(data), False))
    return frames


def summarize_sse(body: bytes) -> Dict[str, int]:
    """Return safe counts; incomplete fragments are not JSON-error events."""
    summary = {
        "complete_frames": 0,
        "partial_frames": 0,
        "done_frames": 0,
        "json_frames": 0,
        "unparsed_complete_frames": 0,
        "finish_reason_frames": 0,
        "error_frames": 0,
    }
    for frame in split_sse_frames(body):
        if not frame.terminated:
            summary["partial_frames"] += 1
            continue
        summary["complete_frames"] += 1
        if frame.data.strip() == b"[DONE]":
            summary["done_frames"] += 1
            continue
        try:
            payload = json.loads(frame.data)
        except (ValueError, UnicodeDecodeError):
            summary["unparsed_complete_frames"] += 1
            continue
        summary["json_frames"] += 1
        if isinstance(payload, dict) and payload.get("error") is not None:
            summary["error_frames"] += 1
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if isinstance(choices, list) and any(
            isinstance(choice, dict) and choice.get("finish_reason") for choice in choices
        ):
            summary["finish_reason_frames"] += 1
    return summary


def summarize_transport(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Fold safe metadata once per observed HTTP attempt, without payload parsing."""
    requests: set[str] = set()
    terminals: Dict[str, Mapping[str, Any]] = {}
    interruptions: set[str] = set()
    errors: set[str] = set()
    error_types: Dict[str, str] = {}
    stored_chunks = 0
    for record in records:
        event = record.get("event")
        prefix = f"{record.get('invocation_id', '')}:{record.get('attempt', '')}:"
        rid = prefix + str(record.get("request_id", record.get("seq", "")))
        if event == "http_request":
            requests.add(prefix + str(record["seq"]))
        elif event == "http_response_complete":
            terminals[rid] = record
        elif event == "http_response_stream_interrupted":
            interruptions.add(rid)
            error_types[rid] = str(record.get("error_type", "unknown"))
        elif event == "http_request_error":
            errors.add(rid)
        if record.get("payload_name") == "response_body":
            stored_chunks += 1
    incomplete = {rid for rid, r in terminals.items() if r.get("state") != "complete"} | interruptions
    missing = requests - terminals.keys() - errors
    lost = sum(
        max(0, int(r.get("bytes_received", 0)) - int(r.get("stored_byte_length", 0)))
        for r in terminals.values()
        if "known_credential_replaced" not in r.get("exclusions", [])
    )
    reasons = []
    if incomplete:
        reasons.append("transport_stream_incomplete")
    if missing:
        reasons.append("transport_response_terminal_missing")
    if lost:
        reasons.append("transport_observed_bytes_not_retained")
    return {
        "http_attempts": len(requests),
        "response_terminals": len(terminals),
        "response_states": dict(Counter(str(r.get("state", "unknown")) for r in terminals.values())),
        "response_statuses": dict(Counter(str(r.get("status", "unknown")) for r in terminals.values())),
        "exact_responses": sum(bool(r.get("exact")) and r.get("state") == "complete" for r in terminals.values()),
        "interrupted_responses": len(interruptions),
        "stream_error_types": dict(Counter(error_types.values())),
        "request_errors": len(errors),
        "missing_response_terminals": len(missing),
        "response_chunks": stored_chunks,
        "bytes_received": sum(int(r.get("bytes_received", 0)) for r in terminals.values()),
        "bytes_forwarded": sum(int(r.get("bytes_forwarded", 0)) for r in terminals.values()),
        "stored_response_bytes": sum(int(r.get("stored_byte_length", 0)) for r in terminals.values()),
        "observed_bytes_not_retained": lost,
        "incomplete_reasons": reasons,
        "scope": "observed HTTP attempts, not logical turns or hidden provider events",
    }
