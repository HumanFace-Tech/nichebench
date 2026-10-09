"""Incremental event journal plus content-addressed payload store.

Journal contract
----------------
``events.jsonl`` is append-only, one JSON object per line, flushed and fsynced
after every record so a hard host kill loses at most the record in flight.

Each record carries:

``seq``            monotonic sequence number within the attempt
``timestamp``      UTC ISO-8601 observation time
``source``         which subsystem observed the event
``invocation_id``  capture run id
``request_id``     transport request id when applicable
``part_id``        message/part id when applicable
``payload_refs``   references into the blob store (never inline payloads)

No record embeds cumulative history. The agent timeline is reconstructed by
folding records in ``seq`` order; storing a growing transcript inside every
record is exactly what this design avoids.

Blob store contract
-------------------
Payloads are split into fixed-size blocks. Each block is written once, keyed by
its own SHA-256, and referenced by ``(sha256, offset, length)``. A repeated
request prefix therefore costs one copy, not one copy per turn. A
``*_manifest.json`` file records the ordered block list for multi-block blobs.

Completeness boundaries
-----------------------
The journal states, per event, which boundary it observes:

``transport_http_entity``  exact bytes on the HTTP wire, before any
                          application-layer interpretation
``application_response``   decoded application view (judge lane only)
``db_snapshot``            SQLite state snapshot, not a wire capture
``agent_timeline``         normalised agent event ordering

Hidden provider-internal reasoning is *not* observable at any of these
boundaries. The journal never claims otherwise.

Ownership: storage only. Callers decide what to record.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from nichebench.execution.runtime.capture.errors import CaptureError, CaptureQuotaExceeded, CaptureWriteError
from nichebench.execution.runtime.capture.run import (
    CaptureAttempt,
    CaptureSettings,
    ensure_private_dir,
    write_private_json,
)
from nichebench.execution.runtime.capture.scrub import StreamingCredentialScrubber, scrub_bytes

BLOCK_SIZE = 64 * 1024

# Head-room kept aside for the descriptor and completion records so a capture
# that hits its payload quota can still explain itself.
RESERVED_DESCRIPTOR_BYTES = 256 * 1024

# Boundary identifiers. Stored verbatim in every event so a reader never has to
# guess whether a record is a wire fact or an interpretation.
BOUNDARY_TRANSPORT = "transport_http_entity"
BOUNDARY_APPLICATION = "application_response"
BOUNDARY_DB_SNAPSHOT = "db_snapshot"
BOUNDARY_AGENT_TIMELINE = "agent_timeline"
BOUNDARY_PROCESS_STREAM = "process_stream"

PayloadRef = Tuple[str, int, int]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BlobRef:
    """A reference to a blob, or a block range within one."""

    sha256: str
    offset: int
    length: int

    def to_dict(self) -> Dict[str, Any]:
        return {"sha256": self.sha256, "offset": self.offset, "length": self.length}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BlobRef":
        return cls(str(raw["sha256"]), int(raw["offset"]), int(raw["length"]))


class BlobStore:
    """Content-addressed block store for one attempt."""

    def __init__(self, root: Path, quota_bytes: int, budget: "_Quota"):
        self.root = ensure_private_dir(root)
        self._quota_bytes = quota_bytes
        self._budget = budget
        self._written: Dict[str, int] = {}

    def _block_path(self, digest: str) -> Path:
        return self.root / f"{digest}.bin"

    def _write_block(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        if digest in self._written:
            return digest
        self._budget.reserve(len(data))
        path = self._block_path(digest)
        if not path.exists():
            tmp = path.with_suffix(".bin.tmp")
            try:
                with open(tmp, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                with contextlib.suppress(OSError):
                    os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            except OSError as exc:
                with contextlib.suppress(OSError):
                    tmp.unlink()
                raise CaptureWriteError(f"capture blob write failed: {exc.__class__.__name__}") from exc
        self._written[digest] = len(data)
        return digest

    def put(self, data: bytes) -> BlobRef:
        """Store ``data`` as blocks and return a reference to the whole blob."""
        blocks = [data[i : i + BLOCK_SIZE] for i in range(0, len(data), BLOCK_SIZE)] or [b""]
        refs: List[PayloadRef] = []
        for block in blocks:
            digest = self._write_block(block)
            refs.append((digest, 0, len(block)))
        if len(refs) > 1:
            manifest = {
                "size": len(data),
                "block_size": BLOCK_SIZE,
                "blocks": [{"sha256": d, "offset": o, "length": n} for d, o, n in refs],
            }
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            digest = self._write_block(manifest_bytes)
            return BlobRef(digest, 0, len(manifest_bytes))
        digest, offset, length = refs[0]
        return BlobRef(digest, offset, length)

    def get(self, ref: BlobRef | Dict[str, Any]) -> bytes:
        """Read back a stored blob. Raises ``KeyError`` when absent."""
        resolved = BlobRef.from_dict(ref) if isinstance(ref, dict) else ref
        block_path = self._block_path(resolved.sha256)
        raw = block_path.read_bytes()
        if len(raw) != resolved.length or resolved.offset != 0:
            raise KeyError(f"blob {resolved.sha256} is not a single-block entry")
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw
        if not isinstance(manifest, dict) or "blocks" not in manifest:
            return raw
        out = bytearray()
        for entry in manifest["blocks"]:
            chunk = self._block_path(str(entry["sha256"])).read_bytes()
            out += chunk[int(entry["offset"]) : int(entry["offset"]) + int(entry["length"])]
        return bytes(out)


class _Quota:
    """Thread-safe byte budget shared by all stores in one capture."""

    def __init__(self, limit_bytes: int):
        self.limit_bytes = limit_bytes
        self._used = 0
        self._lock = threading.Lock()

    def reserve(self, num_bytes: int) -> None:
        """Account for ``num_bytes`` of new data.

        Raises:
            CaptureQuotaExceeded: When the budget would be exceeded. Capture
                never truncates silently.
        """
        with self._lock:
            if self.limit_bytes > 0 and self._used + num_bytes > self.limit_bytes:
                raise CaptureQuotaExceeded(
                    f"runtime capture disk quota exceeded: " f"{self._used + num_bytes} > {self.limit_bytes} bytes"
                )
            self._used += num_bytes

    def force(self, num_bytes: int) -> None:
        """Account for bytes outside the limit.

        Used only by the reserved completion path, so the descriptor can always
        be written and the archive can always explain itself.
        """
        with self._lock:
            self._used += num_bytes

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


class EventJournal:
    """Append-only JSONL journal with fsync-per-record durability."""

    def __init__(
        self,
        attempt: CaptureAttempt,
        invocation_id: str,
        settings: CaptureSettings,
        known_credentials: Optional[List[str]] = None,
        on_stop: Optional[Callable[[str], None]] = None,
    ):
        self.attempt = attempt
        self.invocation_id = invocation_id
        self._settings = settings
        ensure_private_dir(attempt.path)
        self._blobs = BlobStore(attempt.blobs_path, settings.quota_bytes, _Quota(settings.quota_bytes))
        self._seq = 0
        self._lock = threading.RLock()
        self._stopped_reason: Optional[str] = None
        self.known_credentials = known_credentials if known_credentials is not None else []
        self._on_stop = on_stop
        self.incomplete_reasons: List[str] = []
        self._file_capacity: Dict[str, int] = {}
        # Reserved up front so the descriptor and its completion reasons can
        # always be written, even when the payload quota is exhausted.
        self._reserve_descriptor_headroom()
        # Held open for the attempt's lifetime: an append-only journal that is
        # opened once and fsynced per record, so a hard kill loses at most the
        # record in flight.
        self._handle = open(attempt.events_path, "a", encoding="utf-8")  # noqa: SIM115
        with contextlib.suppress(OSError):
            os.chmod(attempt.events_path, 0o600)
        # Reserve nothing for the journal itself here; each record is charged
        # through the same budget via _charge so the quota covers everything.

    # ------------------------------------------------------------------

    def _reserve_descriptor_headroom(self) -> None:
        """Reserve capacity for the completion record and descriptor."""
        # A separate physical reserve, not charged as payload: small configured
        # quotas must still be able to open a journal and explain exhaustion.
        self._reserve_path = self.attempt.path / ".emergency-space"
        try:
            with open(self._reserve_path, "wb") as handle:
                os.chmod(self._reserve_path, 0o600)
                if hasattr(os, "posix_fallocate"):
                    os.posix_fallocate(handle.fileno(), 0, RESERVED_DESCRIPTOR_BYTES)
                else:
                    handle.write(b"\0" * RESERVED_DESCRIPTOR_BYTES)
        except OSError as exc:
            self.release_reserve()
            raise CaptureWriteError("capture emergency reserve unavailable") from exc

    def release_reserve(self) -> None:
        with contextlib.suppress(OSError):
            self._reserve_path.unlink()

    @property
    def blobs(self) -> BlobStore:
        return self._blobs

    @property
    def used_bytes(self) -> int:
        return self._blobs._budget.used

    def reserve_file(self, path: Path, size: int) -> None:
        """Charge only growth of an auxiliary file refreshed in place."""
        key = str(path)
        previous = self._file_capacity.get(key, 0)
        try:
            self._blobs._budget.reserve(max(0, size - previous))
        except CaptureError as exc:
            self.stop_capture(type(exc).__name__)
            raise
        self._file_capacity[key] = max(previous, size)

    def store_payload(
        self,
        name: str,
        data: bytes,
        extra: Optional[Dict[str, Any]] = None,
        known_credentials: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Store bytes and journal a ``blob_stored`` reference record.

        Args:
            name: Logical payload name, e.g. ``request_body`` or ``sse_chunk``.
            data: Bytes to store. These are the *captured* bytes; anything the
                harness forwarded upstream or to the agent was sent unmodified.
            extra: Optional metadata merged into the record.
            known_credentials: Literal credential values to replace before the
                bytes are written. When a replacement happens the record carries
                ``exact: False`` plus an ``exclusions`` entry, so a scrubbed body
                is never presented as a byte-exact capture.

        Returns:
            The emitted journal record.
        """
        exclusions: List[str] = []
        payload = data
        credentials = [*self.known_credentials, *(known_credentials or [])]
        if credentials:
            payload, hits = scrub_bytes(data, credentials)
            if hits:
                exclusions = ["known_credential_replaced"]
        try:
            ref = self._blobs.put(payload)
        except CaptureError as exc:
            self.stop_capture(type(exc).__name__)
            raise
        record: Dict[str, Any] = {
            "event": "blob_stored",
            "payload_name": name,
            "payload_ref": ref.to_dict(),
            "byte_length": len(payload),
            "exact": not exclusions,
        }
        if exclusions:
            record["exclusions"] = exclusions
            record["stored_byte_length"] = len(payload)
            record["captured_byte_length"] = len(data)
        if extra:
            record.update(extra)
        return self.append(
            record,
            boundary=record.get("boundary", BOUNDARY_AGENT_TIMELINE),
            source=record.get("source", "capture"),
            request_id=record.get("request_id"),
        )

    def store_path(self, name: str, path: Path, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Store the contents of a file, if readable.

        Returns:
            The emitted record, or ``None`` when the file is unreadable.
        """
        try:
            data = path.read_bytes()
        except OSError:
            return None
        return self.store_payload(name, data, extra)

    def append_reserved(
        self,
        record: Dict[str, Any],
        boundary: str = BOUNDARY_AGENT_TIMELINE,
        source: str = "capture",
    ) -> Optional[Dict[str, Any]]:
        """Append a completion/descriptor record using the reserved head-room.

        Returns:
            The written record, or ``None`` when even the reserve is exhausted.
            Never raises: recording why capture stopped must not itself fail.
        """
        budget = self._blobs._budget
        self.release_reserve()
        with self._lock:
            record = dict(record)
            self._seq += 1
            record.update(
                seq=self._seq,
                timestamp=_now_iso(),
                invocation_id=self.invocation_id,
                attempt=self.attempt.index,
                attempt_label=self.attempt.label,
            )
            record.setdefault("boundary", boundary)
            record.setdefault("source", source)
            line = json.dumps(record, ensure_ascii=False)
            line, hits = _scrub_journal_line(line, self.known_credentials)
            if hits:
                record = json.loads(line)
            encoded = (line + "\n").encode("utf-8")
            with contextlib.suppress(CaptureQuotaExceeded):
                budget.force(len(encoded))
            try:
                self._handle.write(line + "\n")
                self._handle.flush()
                os.fsync(self._handle.fileno())
            except (OSError, ValueError):
                return None
            return record

    def append(
        self,
        record: Dict[str, Any],
        boundary: str = BOUNDARY_AGENT_TIMELINE,
        source: str = "capture",
        request_id: Optional[str] = None,
        part_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append one record to the journal and fsync it.

        Args:
            record: Event payload (never cumulative history).
            boundary: Observation boundary, one of the ``BOUNDARY_*`` constants.
            source: Subsystem label, e.g. ``transport``, ``agent_stream``,
                ``session_db``, ``judge_lane``.
            request_id: Transport request id when applicable.
            part_id: Message/part id when applicable.
            timestamp: Override observation timestamp (used by stream readers).

        Returns:
            The written record, including ``seq`` and ``timestamp``.

        Raises:
            CaptureQuotaExceeded: If appending the record would exceed quota.
        """
        payload = dict(record)
        payload.setdefault("boundary", boundary)
        with self._lock:
            self._seq += 1
            payload["seq"] = self._seq
            payload["timestamp"] = timestamp or _now_iso()
            payload["source"] = source
            payload["invocation_id"] = self.invocation_id
            payload["attempt"] = self.attempt.index
            payload["attempt_label"] = self.attempt.label
            if request_id is not None:
                payload["request_id"] = request_id
            if part_id is not None:
                payload["part_id"] = part_id
            line = json.dumps(payload, ensure_ascii=False)
            line, hits = _scrub_journal_line(line, self.known_credentials)
            if hits:
                payload = json.loads(line)
            encoded = (line + "\n").encode("utf-8")
            try:
                self._blobs._budget.reserve(len(encoded))
            except CaptureError as exc:
                self.stop_capture(type(exc).__name__)
                raise
            try:
                self._handle.write(line + "\n")
                self._handle.flush()
                os.fsync(self._handle.fileno())
            except (OSError, ValueError) as exc:
                self.stop_capture("capture_journal_write_failed")
                raise CaptureWriteError(f"capture journal write failed: {exc.__class__.__name__}") from exc
            return payload

    def close(self) -> None:
        """Flush and close the journal. Never raises."""
        self.release_reserve()
        with self._lock:
            with contextlib.suppress(OSError, ValueError):
                self._handle.flush()
                os.fsync(self._handle.fileno())
            with contextlib.suppress(OSError, ValueError):
                self._handle.close()

    def write_manifest(self, payload: Dict[str, Any]) -> Path:
        """Write an auxiliary JSON manifest into the attempt directory."""
        return write_private_json(self.attempt.path / f"{payload.get('name', 'manifest')}.json", payload)

    def stop_capture(self, reason: str) -> None:
        """Record that capture has been stopped by a write failure.

        The proxy has no back-reference to the owning session, so it reports
        here directly; :class:`CaptureSession` reads ``stopped`` during
        finalisation and propagates the incomplete state.
        """
        if self._stopped_reason is not None:
            return
        self._stopped_reason = reason
        self.append_reserved({"event": "capture_storage_stopped", "reason": reason, "completion": "incomplete"})
        if self._on_stop is not None:
            self._on_stop(reason)

    def mark_incomplete(self, reason: str) -> None:
        if reason not in self.incomplete_reasons:
            self.incomplete_reasons.append(reason)

    @property
    def stopped(self) -> Optional[str]:
        """Reason capture was stopped for this journal, or ``None``."""
        return self._stopped_reason


class SpoolWriter:
    """Binary spool for a process byte stream, with observation timestamps.

    Bytes are written as they arrive and a journal event per chunk records the
    offset and the observation time, so a consumer gets both the byte sequence
    and its arrival timing.

    When the journal carries known credential values, the *stored* copy is
    scrubbed across chunk boundaries. The agent's own stdout/stderr — what it
    actually saw — is never rewritten; only the archive is protected.
    """

    def __init__(
        self,
        journal: EventJournal,
        path: Path,
        stream_name: str,
        known_credentials: Optional[Iterable[str]] = None,
    ):
        ensure_private_dir(path.parent)
        self._journal = journal
        self._path = path
        self._stream_name = stream_name
        self._known_credentials = known_credentials if known_credentials is not None else journal.known_credentials
        self._scrubber = StreamingCredentialScrubber([])
        self._started = False
        # Held open so bytes land on disk as they arrive; see EventJournal.
        self._handle = open(path, "wb")  # noqa: SIM115
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        self._offset = 0
        self._captured = 0
        self._lock = threading.Lock()
        self._closed = False

    @property
    def bytes_written(self) -> int:
        """Bytes actually written to the (possibly scrubbed) spool file."""
        with self._lock:
            return self._offset

    @property
    def captured_bytes(self) -> int:
        """Bytes observed on the stream, before any scrubbing."""
        with self._lock:
            return self._captured

    @property
    def redacted(self) -> bool:
        """True when at least one known credential was replaced."""
        return self._scrubber.redacted

    @property
    def path(self) -> Path:
        return self._path

    def write(self, chunk: bytes) -> None:
        """Append a chunk and journal an observation record.

        Raises:
            CaptureQuotaExceeded: If the journal or spool cannot accept the
                record. The caller (the cage reader loop) treats this as a
                capture-stop condition rather than retrying forever.
        """
        if not chunk:
            return
        if not self._started:
            self._scrubber = StreamingCredentialScrubber(self._known_credentials)
            self._started = True
        storable = self._scrubber.feed(chunk)
        with self._lock:
            offset = self._offset
            if storable:
                self._journal.blobs._budget.reserve(len(storable))
                try:
                    self._handle.write(storable)
                    self._handle.flush()
                    os.fsync(self._handle.fileno())
                except OSError as exc:
                    raise CaptureWriteError("capture spool write failed") from exc
                self._offset += len(storable)
            self._captured += len(chunk)
        self._journal.append(
            {
                "event": "stream_chunk",
                "stream": self._stream_name,
                "offset": offset,
                "byte_length": len(storable),
                "captured_byte_length": len(chunk),
                "exact": not self._scrubber.redacted,
            },
            boundary=BOUNDARY_PROCESS_STREAM,
            source="process_stream",
            timestamp=_now_iso(),
        )

    def close(self) -> None:
        """Flush the scrubber tail and close the spool. Never raises."""
        if self._closed:
            return
        self._closed = True
        tail = self._scrubber.flush()
        with self._lock:
            if tail:
                try:
                    self._journal.blobs._budget.reserve(len(tail))
                    self._handle.write(tail)
                    self._offset += len(tail)
                except (OSError, ValueError, CaptureError):
                    self._journal.stop_capture("capture_spool_tail_write_failed")
        with contextlib.suppress(OSError, ValueError):
            self._handle.flush()
            os.fsync(self._handle.fileno())
        with contextlib.suppress(OSError, ValueError):
            self._handle.close()
        self._journal.append_reserved(
            {
                "event": "stream_closed",
                "stream": self._stream_name,
                "stored_byte_length": self._offset,
                "captured_byte_length": self._captured,
                "exact": not self.redacted,
                "exclusions": ["known_credential_replaced"] if self.redacted else [],
            },
            boundary=BOUNDARY_PROCESS_STREAM,
            source="process_stream",
        )


def _scrub_journal_line(line: str, known: Iterable[str]) -> Tuple[str, List[str]]:
    from nichebench.execution.runtime.capture.scrub import scrub_known_credentials

    # JSON escaping can transform the literal; include the serialized spelling.
    credentials = list(known)
    credentials.extend(json.dumps(c, ensure_ascii=False)[1:-1] for c in list(credentials))
    clean, hits = scrub_known_credentials(line, credentials)
    if hits:
        payload = json.loads(clean)
        payload["exact"] = False
        payload["exclusions"] = [*payload.get("exclusions", []), "known_credential_replaced"]
        clean = json.dumps(payload, ensure_ascii=False)
    return clean, hits


def read_events(attempt_path: Path) -> List[Dict[str, Any]]:
    """Read every journal record from an attempt directory (best-effort)."""
    events_path = Path(attempt_path)
    if events_path.is_dir():
        events_path = events_path / "events.jsonl"
    records: List[Dict[str, Any]] = []
    if not events_path.exists():
        return records
    with open(events_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                records.append({"event": "journal_line_unparsable", "raw_length": len(line)})
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def collect_payload_refs(records: Iterable[Dict[str, Any]]) -> List[BlobRef]:
    """Collect every blob reference referenced by journal records."""
    refs: List[BlobRef] = []
    for record in records:
        ref = record.get("payload_ref")
        if isinstance(ref, dict) and "sha256" in ref:
            refs.append(BlobRef.from_dict(ref))
    return refs


def resolve_sequence(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return records ordered by ``seq``.

    Ordering is per-attempt and per-journal only. No cross-source global order
    is implied.
    """
    return sorted(records, key=lambda r: int(r.get("seq", 0)))
