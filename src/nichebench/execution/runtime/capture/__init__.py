"""Runtime capture — the full-trace capture subsystem for cage runtime tasks.

Purpose
-------
Answer, for one runtime task, exactly what happened:

- the **exact HTTP bytes** sent to and received from the pinned MiniMax route,
- the **exact agent process streams** (stdout/stderr) with observation times,
- the **full agent event timeline** (text, reasoning, tool inputs with full
  write payloads, tool results, errors) from ``--format json --thinking``,
- a **WAL-consistent snapshot plus a full row export** of OpenCode's session
  store — all sessions, all rows, raw JSON, IDs preserved,
- the **application boundary** of the judge lane (arguments plus the provider's
  public response before the harness strips it).

What it deliberately does not claim
----------------------------------
- Hidden provider-internal reasoning is not observable and is never invented.
- The judge lane is an application-boundary capture, not a wire capture.
- ``agent_timeline`` ordering is derived from OpenCode's own per-attempt
  timestamps; it is not a claim of global event order.
- When a credential had to be scrubbed, the affected artifact is marked
  redacted rather than presented as an exact copy.

Layout
------
``<results_outdir>/captures/<uuid>/`` with immutable numbered
``attempts/attempt_NNN/`` directories. See :mod:`run` for the layout contract
and :mod:`store` for the journal/blob contract.

Enabling
--------
``runtime_capture_enable: true``. Only the pinned ``minimax_m3`` route is
supported; any other route fails loudly instead of producing a partial claim.
Retention must be ``standard`` or ``full``.

Public API
----------
:class:`CaptureSettings`, :class:`CaptureRun`, :class:`CaptureSession`.
"""

from __future__ import annotations

from nichebench.execution.runtime.capture.errors import (
    CaptureError,
    CaptureQuotaExceeded,
    CaptureUnsupported,
    CaptureWriteError,
)
from nichebench.execution.runtime.capture.proxy import DEFAULT_UPSTREAM, ProxyConfig, ProxyHandle, ProxyUnavailable
from nichebench.execution.runtime.capture.run import CaptureAttempt, CaptureRun, CaptureSettings
from nichebench.execution.runtime.capture.session import CaptureSession
from nichebench.execution.runtime.capture.store import (
    BOUNDARY_AGENT_TIMELINE,
    BOUNDARY_APPLICATION,
    BOUNDARY_DB_SNAPSHOT,
    BOUNDARY_PROCESS_STREAM,
    BOUNDARY_TRANSPORT,
    BlobStore,
    EventJournal,
    SpoolWriter,
    read_events,
    resolve_sequence,
)

__all__ = [
    "BOUNDARY_AGENT_TIMELINE",
    "BOUNDARY_APPLICATION",
    "BOUNDARY_DB_SNAPSHOT",
    "BOUNDARY_PROCESS_STREAM",
    "BOUNDARY_TRANSPORT",
    "BlobStore",
    "CaptureAttempt",
    "CaptureError",
    "CaptureQuotaExceeded",
    "CaptureRun",
    "CaptureSession",
    "CaptureSettings",
    "CaptureUnsupported",
    "CaptureWriteError",
    "DEFAULT_UPSTREAM",
    "EventJournal",
    "ProxyConfig",
    "ProxyHandle",
    "ProxyUnavailable",
    "SpoolWriter",
    "read_events",
    "resolve_sequence",
]
