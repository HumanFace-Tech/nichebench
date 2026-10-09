"""Capture session — the run-local object that owns journals and finalisation.

``CaptureSession`` is created once per runtime test case from
``executor.results_outdir``, *before* the cage launches, and passed explicitly
through the runtime flow. Nothing is stored on the executor, so two parallel
tasks never share a recorder.

Each agent attempt (initial, retry, review nudge) gets its own immutable
numbered directory and its own :class:`EventJournal`. The session also owns
judge/application-lane captures, so a retry never overwrites the previous
attempt's journal.

Finalisation order matters and is enforced here: readers drain, the proxy is
cancelled, the session store is snapshotted and exported, and only then is the
descriptor written. A finalisation failure is recorded and never prevents the
rest of finalisation — or the workspace cleanup that follows it.

Ownership: session lifecycle. See :mod:`store` for journal mechanics,
:mod:`session_db` for DB archiving, :mod:`proxy` for transport.
"""

from __future__ import annotations

import contextlib
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

from nichebench.execution.runtime.capture.agent_stream import parse_opencode_json_stream
from nichebench.execution.runtime.capture.errors import CaptureError
from nichebench.execution.runtime.capture.identity import (
    capture_opencode_config,
    inspect_image_identity,
    probe_opencode_version,
)
from nichebench.execution.runtime.capture.inspection import DESCRIPTOR_VERSION, summarize_transport
from nichebench.execution.runtime.capture.proxy import ProxyHandle, start_capture_proxy
from nichebench.execution.runtime.capture.run import (
    CaptureAttempt,
    CaptureRun,
    ensure_private_dir,
    reference_from_descriptor,
    write_private_json,
)
from nichebench.execution.runtime.capture.scrub import scrub_known_credentials
from nichebench.execution.runtime.capture.session_db import archive_session_state, consistent_backup, find_db
from nichebench.execution.runtime.capture.store import (
    BOUNDARY_AGENT_TIMELINE,
    BOUNDARY_APPLICATION,
    BOUNDARY_DB_SNAPSHOT,
    BOUNDARY_TRANSPORT,
    EventJournal,
    SpoolWriter,
    read_events,
)

# Minimum seconds between in-run session-store refreshes.
DB_CHECKPOINT_INTERVAL_SECONDS = 15.0

COMPLETE = "complete"
INCOMPLETE = "incomplete"


class CaptureSession:
    """Owns every capture artifact for one runtime test case."""

    def __init__(self, run: CaptureRun, known_credentials: Optional[List[str]] = None):
        self.run = run
        self.known_credentials = [c for c in (known_credentials or []) if c]
        self._capture_stopped = False
        self._journals: Dict[int, EventJournal] = {}
        self._spools: List[SpoolWriter] = []
        self._proxy: Optional[ProxyHandle] = None
        self._lane = "judge"
        self._label = ""
        self._completion = COMPLETE
        self._reasons: List[str] = []
        self._context: Dict[str, Any] = {}
        self._checkpoint_monotonic: Dict[int, float] = {}
        self._finalised_attempts: set[int] = set()
        self._final_descriptor: Optional[Dict[str, Any]] = None

    def _storage_failed(self, reason: str) -> None:
        # Called from proxy/reader threads: never join the proxy from its own
        # event loop. The owning cage observes this flag and terminates its child.
        self._capture_stopped = True
        self.mark_incomplete(reason)

    def _new_journal(self, attempt: CaptureAttempt) -> EventJournal:
        try:
            return EventJournal(
                attempt, self.run.invocation_id, self.run.settings, self.known_credentials, self._storage_failed
            )
        except (CaptureError, OSError) as exc:
            self._storage_failed(f"capture_startup_failed:{type(exc).__name__}")
            self._write_descriptor(self.descriptor())
            raise

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.run.settings.enabled

    @property
    def root(self) -> Path:
        return self.run.root

    def note(self, reason: str) -> None:
        """Record a completion reason without failing the capture."""
        if reason not in self._reasons:
            self._reasons.append(reason)

    def mark_incomplete(self, reason: str) -> None:
        """Record that the capture cannot be trusted as complete."""
        self._completion = INCOMPLETE
        self.note(reason)

    def lane_context(self, lane: str, label: str) -> None:
        """Set the lane/label applied to subsequent application-boundary records."""
        self._lane = lane
        self._label = label

    @property
    def current_lane(self) -> str:
        return self._lane

    @property
    def current_label(self) -> str:
        return self._label

    def note_app_call(self, model: str, arguments: Dict[str, Any]) -> None:
        """Record the sanitised arguments of an application-boundary call.

        The response is recorded separately by
        :meth:`record_application_call`, so both sides of the boundary are
        present even when only one of them was captured.
        """
        attempt = self.run.judges[-1] if self.run.judges else self.open_judge_attempt(self._label or "app_call")
        journal = self.journal_for(attempt, judge=True)
        journal.append(
            {
                "event": "application_request",
                "lane": self._lane,
                "label": self._label,
                "model": model,
                "arguments": arguments,
                "boundary_note": "application_response (LiteLLM call arguments; not a wire capture)",
            },
            boundary=BOUNDARY_APPLICATION,
            source="judge_lane",
        )

    def set_context(self, **values: Any) -> None:
        """Attach non-secret execution context to the descriptor."""
        self._context.update(values)

    @property
    def context(self) -> Dict[str, Any]:
        return dict(self._context)

    # ------------------------------------------------------------------
    # Attempts
    # ------------------------------------------------------------------

    def open_attempt(self, label: str, judge: bool = False) -> CaptureAttempt:
        """Allocate and open a journal for the next immutable attempt."""
        self._final_descriptor = None
        attempt = self.run.next_attempt(label, judge=judge)
        journal = self._new_journal(attempt)
        self._journals[attempt.index if not judge else 10_000 + attempt.index] = journal
        self.note(f"attempt_{attempt.index:03d}:{label}")
        return attempt

    def journal_for(self, attempt: CaptureAttempt, judge: bool = False) -> EventJournal:
        """Return the journal for ``attempt``."""
        key = attempt.index if not judge else 10_000 + attempt.index
        journal = self._journals.get(key)
        if journal is None:
            journal = self._new_journal(attempt)
            self._journals[key] = journal
        return journal

    def spool(self, attempt: CaptureAttempt, stream_name: str) -> SpoolWriter:
        """Create a binary spool for one attempt's process stream.

        The stored copy is scrubbed of known credentials across chunk
        boundaries; the agent's own stream is not rewritten.
        """
        journal = self.journal_for(attempt)
        target = attempt.stdout_path if stream_name == "stdout" else attempt.stderr_path
        spool = SpoolWriter(journal, target, stream_name, known_credentials=self.known_credentials)
        self._spools.append(spool)
        return spool

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def start_proxy(
        self,
        journal: EventJournal,
        upstream: str,
        upstream_auth: Optional[str] = None,
    ) -> ProxyHandle:
        """Start the transport capture proxy for one attempt.

        Args:
            journal: Attempt journal receiving transport events.
            upstream: Fixed provider origin, e.g. ``https://api.minimax.io/v1``.
            upstream_auth: Real provider credential. The proxy swaps it in for
                the capability token on forwarded requests and scrubs it from
                stored bodies. Held in memory only; never journalled.

        Returns:
            The live proxy handle.
        """
        from nichebench.execution.runtime.capture.proxy import ProxyConfig

        config = ProxyConfig(upstream=upstream)
        scrub_list = list(self.known_credentials)
        if upstream_auth:
            scrub_list.extend(value for value in upstream_auth.split(" ", 1) if len(value) >= 8 and " " not in value)
        handle = start_capture_proxy(
            journal,
            config,
            upstream_auth=upstream_auth,
            known_credentials=scrub_list,
        )
        self._proxy = handle
        return handle

    def probe_health(self, image: str, timeout_seconds: float = 30.0) -> Dict[str, Any]:
        """Verify container reachability with an in-container curl probe.

        Uses the same image the agent will run and makes no provider call, so a
        connectivity problem is diagnosed before the agent burns a run.
        """
        from nichebench.execution.runtime.capture.proxy import build_health_probe_command, probe_from_container

        handle = self._proxy
        if handle is None:
            return {"probed": False, "reason": "proxy_not_running"}
        command = build_health_probe_command(image)
        health_url = f"http://{handle.config.advertise_host}:{handle.port}" f"{handle.capability_path}/healthz"
        healthy, detail = probe_from_container(command, health_url, timeout_seconds=timeout_seconds)
        attempt = self.run.attempts[-1] if self.run.attempts else None
        if attempt is not None:
            self.journal_for(attempt).append(
                {
                    "event": "container_health_probe",
                    "probed": True,
                    "healthy": healthy,
                    "detail": detail,
                    "image": image,
                    "tool": "curl",
                    "paid_call": False,
                },
                boundary=BOUNDARY_TRANSPORT,
                source="transport",
            )
        return {"probed": True, "healthy": healthy, "detail": detail, "image": image}

    def stop_proxy(self) -> None:
        """Cancel in-flight proxy exchanges. Never raises."""
        handle = self._proxy
        if handle is not None:
            with contextlib.suppress(Exception):
                handle.stop()
            self._proxy = None

    # ------------------------------------------------------------------
    # Agent stream
    # ------------------------------------------------------------------

    def record_agent_stream(self, journal: EventJournal, stdout_text: str, scoring_text: str) -> Dict[str, Any]:
        """Journal the parsed agent JSON stream and the derived scoring text.

        The scoring text is what the cage returns as ``mut_output``, so this is
        a record of the run's own output rather than a substitute for it.
        """
        lines = stdout_text.splitlines()
        for index, line in enumerate(lines, 1):
            journal.store_payload(
                "agent_event",
                line.encode("utf-8"),
                extra={
                    "line": index,
                    "boundary": BOUNDARY_AGENT_TIMELINE,
                    "source": "agent_stream",
                    "native_event": True,
                },
            )

        def _emit(record: Dict[str, Any]) -> None:
            payload = {k: v for k, v in record.items() if k != "line"}
            journal.append(
                payload,
                boundary=BOUNDARY_AGENT_TIMELINE,
                source="agent_stream",
                part_id=str(record.get("part_id") or "") or None,
            )

        summary = parse_opencode_json_stream(lines, _emit)
        if not summary["parse_complete"]:
            self.mark_incomplete("agent_stream_had_unparsed_lines")
        journal.append(
            {
                "event": "agent_stream_summary",
                "messages": len(summary["messages"]),
                "tool_calls": summary["tool_calls"],
                "tool_results": summary["tool_results"],
                "reasoning_parts": summary["reasoning_parts"],
                "errors": summary["errors"][:20],
                "non_json_lines": summary["non_json_lines"],
                "json_list_lines": summary["json_list_lines"],
                "unparsed_lines": summary["unparsed_lines"],
                "parse_complete": summary["parse_complete"],
                "scoring_text_matches_stream": (scoring_text or "").strip() == summary["assistant_text"].strip(),
            },
            boundary=BOUNDARY_AGENT_TIMELINE,
            source="agent_stream",
        )
        return summary

    # ------------------------------------------------------------------
    # Judge / application lane
    # ------------------------------------------------------------------

    def record_application_response(self, model: str, response: Any) -> None:
        """Record the provider's public response object, before the harness strips it.

        This is the response half of the ``application_response`` boundary. The
        object is stored verbatim (serialised with a string fallback) so a reader
        can see exactly what the provider returned rather than the harness's
        ``{"model", "output"}`` projection.
        """
        attempt = self._judge_attempt()
        journal = self.journal_for(attempt, judge=True)
        record = journal.store_payload(
            "application_response",
            _json_bytes(response),
            extra={
                "lane": self._lane,
                "label": self._label,
                "model": model,
                "before_strip": True,
                "boundary": BOUNDARY_APPLICATION,
                "source": "judge_lane",
            },
            known_credentials=self.known_credentials,
        )
        journal.append(
            {
                "event": "application_response_recorded",
                "lane": self._lane,
                "label": self._label,
                "model": model,
                "payload_ref": record["payload_ref"],
                "before_strip": True,
            },
            boundary=BOUNDARY_APPLICATION,
            source="judge_lane",
        )

    def record_application_error(self, model: str, error_type: str) -> None:
        """Record a failed application-boundary call.

        Only the exception *type* is stored: a message or ``repr`` can embed
        provider URLs, request bodies, or credentials.
        """
        attempt = self._judge_attempt()
        journal = self.journal_for(attempt, judge=True)
        journal.append(
            {
                "event": "application_error",
                "lane": self._lane,
                "label": self._label,
                "model": model,
                "error_type": error_type,
            },
            boundary=BOUNDARY_APPLICATION,
            source="judge_lane",
        )

    def _judge_attempt(self) -> CaptureAttempt:
        return self.run.judges[-1] if self.run.judges else self.open_judge_attempt(self._label or "app_call")

    def open_judge_attempt(self, label: str, judge: bool = True) -> CaptureAttempt:
        """Allocate a judge-lane attempt directory."""
        self._final_descriptor = None
        attempt = self.run.next_attempt(label, judge=judge)
        journal = self._new_journal(attempt)
        self._journals[10_000 + attempt.index] = journal
        self.note(f"judge_{attempt.index:03d}:{label}")
        return attempt

    # ------------------------------------------------------------------
    # Identity + finalisation
    # ------------------------------------------------------------------

    def capture_identity(self, attempt: CaptureAttempt, image: Optional[str]) -> Dict[str, Any]:
        """Record image identity and the sanitised effective agent config."""
        journal = self.journal_for(attempt)
        identity: Dict[str, Any] = {"image": image}
        if image:
            identity.update(inspect_image_identity(image))
            identity["opencode_version"] = probe_opencode_version(image)
        journal.append(
            {"event": "runtime_identity", **identity},
            boundary=BOUNDARY_AGENT_TIMELINE,
            source="identity",
        )
        return identity

    def capture_effective_config(
        self,
        attempt: CaptureAttempt,
        config_path: Path,
    ) -> Dict[str, Any]:
        """Capture the sanitised generated OpenCode config for this attempt."""
        return capture_opencode_config(config_path, attempt.path, self.known_credentials)

    def checkpoint_session(
        self,
        attempt: CaptureAttempt,
        min_interval_seconds: float = DB_CHECKPOINT_INTERVAL_SECONDS,
    ) -> bool:
        """Refresh the session-store backup in place, rate-limited.

        The end-of-attempt archive is the authoritative copy, but a host kill
        mid-run would otherwise leave no DB snapshot at all. This keeps a
        current one on disk without paying for a backup on every poll tick.

        Args:
            attempt: Attempt whose ``opencode-db`` snapshot is refreshed.
            min_interval_seconds: Minimum seconds between refreshes.

        Returns:
            True when a refresh was performed.
        """
        import time

        data_dir = self._context.get("cage_xdg_data_host")
        if not isinstance(data_dir, Path):
            return False
        now = time.monotonic()
        last = self._checkpoint_monotonic.get(attempt.index)
        if last is not None and now - last < min_interval_seconds:
            return False
        self._checkpoint_monotonic[attempt.index] = now
        journal = self.journal_for(attempt)
        try:
            source = find_db(data_dir)
            if source is None:
                return False
            final_dir = ensure_private_dir(attempt.db_snapshot_path)
            backup = consistent_backup(
                source,
                final_dir,
                known_credentials=self.known_credentials,
                reserve_bytes=lambda size: journal.reserve_file(final_dir / "opencode.db", size),
            )
            if backup is None:
                self.mark_incomplete("db_checkpoint_withheld_or_failed")
                return False
            journal.append(
                {
                    "event": "db_checkpoint",
                    "path": str(final_dir / "opencode.db"),
                    "wal_consistent": True,
                    "exact": not self.known_credentials,
                    "credential_policy": "sanitised copy; unsafe snapshots withheld",
                },
                boundary=BOUNDARY_DB_SNAPSHOT,
                source="session_db",
            )
            return True
        except (CaptureError, OSError) as exc:
            self._storage_failed(f"db_checkpoint_storage_failed:{type(exc).__name__}")
        except Exception as exc:
            self.mark_incomplete(f"db_checkpoint_failed:{type(exc).__name__}")
        return False

    def archive_session(
        self,
        attempt: CaptureAttempt,
        data_dir: Optional[Path],
        max_rows_per_table: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Snapshot, scrub, and export the OpenCode session store for one attempt."""
        journal = self.journal_for(attempt)
        try:
            summary = archive_session_state(
                data_dir,
                attempt.path,
                journal,
                max_rows_per_table=max_rows_per_table,
                known_credentials=self.known_credentials,
            )
        except CaptureError as exc:
            self.stop_capture(f"session_archive_storage_failed:{type(exc).__name__}")
            raise
        except Exception as exc:  # noqa: BLE001 - archival is best-effort but recorded
            summary = {"error": "archive_failed", "detail": type(exc).__name__}
            self.mark_incomplete(f"session_archive_failed:{type(exc).__name__}")
        if summary.get("error"):
            self.mark_incomplete(f"session_archive:{summary['error']}")
        if summary.get("export", {}).get("error"):
            self.mark_incomplete(f"session_export:{summary['export']['error']}")
        return summary

    @property
    def capture_stopped(self) -> bool:
        """True once capture has been stopped by a quota or disk failure.

        The cage stops reading spools and the proxy stops accepting new work, so
        a full disk cannot turn into an opaque 500 or an endless retry.
        """
        return self._capture_stopped or any(j.stopped for j in self._journals.values())

    def stop_capture(self, reason: str) -> None:
        """Stop capture for the rest of the attempt, explicitly.

        Called on a quota breach or a disk failure. The descriptor is still
        written — the reserved head-room exists for exactly this — and the
        reason is recorded so the archive is never mistaken for complete.
        """
        if self._capture_stopped:
            return
        self._capture_stopped = True
        self.mark_incomplete(reason)
        for journal in self._journals.values():
            with contextlib.suppress(Exception):
                journal.stop_capture(reason)
        self.stop_proxy()

    def has_agent_activity(self, attempt: Optional[CaptureAttempt]) -> bool:
        """True when this attempt actually produced agent-observable output.

        Used to decide whether a missing session store is a real gap or simply
        "the agent never ran" — the latter must not be reported as incomplete.
        """
        if attempt is None:
            return False
        from nichebench.execution.runtime.capture.store import read_events

        for record in read_events(attempt.path):
            if record.get("event") in ("stream_chunk", "agent_part", "http_request", "agent_text"):
                return True
        return False

    def descriptor(self, completion: Optional[str] = None) -> Dict[str, Any]:
        """Build the descriptor payload without writing it."""
        if completion is not None:
            self._completion = completion
        summary = summarize_transport(record for attempt in self.run.attempts for record in read_events(attempt.path))
        for reason in summary["incomplete_reasons"]:
            self.mark_incomplete(reason)
        payload = self.run.descriptor(self._completion, self._reasons)
        payload["descriptor_version"] = DESCRIPTOR_VERSION
        payload["transport_summary"] = summary
        payload["context"] = _json_safe(self._context)
        payload["boundaries"] = {
            "transport_http_entity": (
                "HTTP entity bytes as forwarded; exact only where labelled, compressed bodies withheld"
            ),
            "process_stream": "agent stdout/stderr bytes with observation times; known credentials scrubbed",
            "agent_timeline": "OpenCode JSON events and DB-derived ordering (per-attempt, not global)",
            "db_snapshot": "WAL-consistent SQLite backup (sanitised or withheld) + typed lossless row export",
            "application_response": "LiteLLM arguments and public response (judge lane; not a wire capture)",
            "not_observable": "hidden provider-internal reasoning and provider-side event order",
        }
        payload["credential_policy"] = (
            "Auth headers and provider keys are never persisted. Known credential "
            "values are scrubbed from payloads and the affected artifact is marked redacted. "
            "Unknown credentials in arbitrary content cannot be guaranteed absent."
        )
        return payload

    def finalise(
        self,
        attempt: Optional[CaptureAttempt],
        completion_reason: Optional[str] = None,
    ) -> Optional[Path]:
        """Drain readers, stop the proxy, archive the DB, and write the descriptor.

        Safe to call exactly once per session and safe to call on the failure
        path: the descriptor is written even when the archive failed, and the
        failure is recorded in ``reasons`` instead of raised.
        """
        if attempt is not None and attempt.index in self._finalised_attempts:
            return self._write_descriptor(self.descriptor())
        if attempt is not None:
            self._finalised_attempts.add(attempt.index)
        if completion_reason:
            self.note(completion_reason)

        for spool in self._spools:
            if attempt is not None and spool.path.parent != attempt.path:
                continue
            with contextlib.suppress(Exception):
                spool.close()
        self.stop_proxy()

        if attempt is not None:
            journal = self._journals.get(attempt.index)
            if journal is None:
                return self._write_descriptor(self.descriptor())
            data_dir = self._context.get("cage_xdg_data_host")
            if self.has_agent_activity(attempt):
                with contextlib.suppress(CaptureError):
                    self.archive_session(attempt, data_dir if isinstance(data_dir, Path) else None)
            else:
                self.note("agent_never_produced_output")
            if journal.stopped:
                self.mark_incomplete(f"capture_stopped:{journal.stopped}")
            for reason in journal.incomplete_reasons:
                self.mark_incomplete(reason)
            journal.append_reserved(
                {
                    "event": "capture_attempt_finalised",
                    "boundary": BOUNDARY_AGENT_TIMELINE,
                    "source": "capture",
                    "completion": self._completion,
                    "reasons": list(self._reasons),
                    "bytes_written": journal.used_bytes,
                }
            )

        for key, journal in self._journals.items():
            if journal.stopped:
                self.mark_incomplete(f"capture_stopped:{journal.stopped}")
            for reason in journal.incomplete_reasons:
                self.mark_incomplete(reason)
            if attempt is not None and key != attempt.index:
                continue
            with contextlib.suppress(Exception):
                journal.close()

        descriptor = self.descriptor()
        path = self._write_descriptor(descriptor)
        if attempt is None and path is not None:
            self._final_descriptor = copy.deepcopy(descriptor)
        return path

    def _write_descriptor(self, descriptor: Dict[str, Any]) -> Optional[Path]:
        path = self.run.root / "capture.json"
        try:
            write_private_json(path, descriptor)
        except Exception:
            return None
        return path if path.exists() else None

    # ------------------------------------------------------------------
    # Result integration
    # ------------------------------------------------------------------

    def result_reference(self) -> Optional[Dict[str, Any]]:
        """Small capture descriptor for the runtime artifact bundle.

        Returned as a dict so the executor can attach it to
        ``result.runtime_artifacts``. Only the pointer travels — the archive is
        never inlined into ``details.jsonl``.
        """
        if not self.run.root.exists():
            return None
        descriptor = self._final_descriptor if self._final_descriptor is not None else self.descriptor()
        return reference_from_descriptor(descriptor)


def _json_safe(value: Any) -> Any:
    """Coerce arbitrary context values into JSON-serialisable data.

    Execution context can hold live objects (a workspace, a process handle).
    The descriptor must stay writable regardless, so anything unserialisable is
    replaced by its type name rather than breaking capture finalisation.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return f"<{type(value).__name__}>"


def _json_bytes(payload: Any) -> bytes:
    """Serialise a provider response object to bytes.

    Provider SDK response objects are not plain dicts. They are normalised by
    walking their public fields (``model_dump``/``dict``/``__dict__``) so the
    captured bytes are the *content* of the response, not an ``object at 0x…``
    repr. A bare object repr would be worse than useless in a trace.
    """
    import json

    try:
        return json.dumps(_normalise_response(payload), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        return str(payload).encode("utf-8")


def _normalise_response(payload: Any) -> Any:
    """Convert a provider response object into JSON-serialisable data."""
    if payload is None or isinstance(payload, (str, int, float, bool)):
        return payload
    if isinstance(payload, dict):
        return {str(key): _normalise_response(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_normalise_response(item) for item in payload]
    for attr in ("model_dump", "to_dict", "dict", "json"):
        method = getattr(payload, attr, None)
        if callable(method):
            try:
                return _normalise_response(method())
            except Exception:
                continue
    if hasattr(payload, "__dict__"):
        return {
            str(key): _normalise_response(value) for key, value in vars(payload).items() if not str(key).startswith("_")
        }
    return str(payload)


__all__ = ["CaptureSession", "scrub_known_credentials"]
