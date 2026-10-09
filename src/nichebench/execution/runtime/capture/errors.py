"""Capture-specific error types.

Ownership: dependency-free leaf module. Every other capture module may import
these without creating a cycle.
"""

from __future__ import annotations


class CaptureError(RuntimeError):
    """Base class for capture failures that must stop the run."""


class CaptureQuotaExceeded(CaptureError):
    """Raised when a capture write would exceed the configured disk quota.

    Capture never silently truncates. A quota breach raises so the owning
    runtime flow can mark the capture incomplete and stop the run.
    """


class CaptureUnsupported(CaptureError):
    """Raised when capture is requested for a configuration it cannot support."""


class CaptureWriteError(CaptureError):
    """Raised when a payload cannot be written: disk full, quota, or I/O error.

    Distinct from :class:`CaptureQuotaExceeded` so the caller can react to
    either, and deliberately *not* surfaced to the agent: a full archive must
    stop capture, not turn into an opaque HTTP 500 or an endless retry.
    """
