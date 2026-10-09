"""Capture layout constants and ``CaptureRun`` construction.

A capture lives at ``<results_outdir>/captures/<uuid>/``:

    captures/<uuid>/
      capture.json                  run descriptor (written at finalisation)
      attempts/
        attempt_001/
          events.jsonl             incremental journal (append-only, fsync)
          blobs/<sha256>.bin       content-addressed payload blocks
          stdout.bin, stderr.bin   raw agent byte streams
          opencode.json            effective generated config, keys removed
          opencode-db/             durable session store snapshots

Design rules encoded here:

- The capture root is **outside** any task workspace, so task cleanup never
  removes the archive.
- Directories are private (``0700``) and files private (``0600``); captures are
  fine-tuning material and stay host-local.
- Attempts are immutable and numbered. A retry or review pass opens a new
  directory; nothing is ever overwritten in place.
- No mutable executor-global recorder exists. ``CaptureRun`` is created per
  task run and passed down explicitly.

Ownership: layout only. Journal/blob writes live in :mod:`store`.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from nichebench.execution.runtime.capture.errors import CaptureUnsupported

DIR_MODE = 0o700
FILE_MODE = 0o600

CAPTURES_DIRNAME = "captures"
ATTEMPTS_DIRNAME = "attempts"
DEFAULT_DISK_QUOTA_BYTES = 4 * 1024 * 1024 * 1024
SUPPORTED_RETENTION = ("standard", "full")
MINIMAL_RETENTION_REJECTED = "minimal"


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) as a private directory."""
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, DIR_MODE)
    return path


def write_private_text(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` with private permissions."""
    ensure_private_dir(path.parent)
    path.write_text(text, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, FILE_MODE)
    return path


def write_private_json(path: Path, payload: Any) -> Path:
    """Serialise ``payload`` as private JSON."""
    return write_private_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


@dataclass(frozen=True)
class CaptureSettings:
    """Resolved capture configuration for one run."""

    enabled: bool
    quota_bytes: int = DEFAULT_DISK_QUOTA_BYTES
    retention: str = "standard"
    supported_routes: tuple[str, ...] = ("minimax_m3",)
    route: Optional[str] = None

    @classmethod
    def from_runtime_config(
        cls,
        runtime_config: Dict[str, Any],
        route: Optional[str] = None,
    ) -> "CaptureSettings":
        """Build settings from the evaluation config dict.

        Args:
            runtime_config: Full evaluation configuration.
            route: Resolved capture route identifier (e.g. ``minimax_m3``).

        Raises:
            CaptureUnsupported: If capture is enabled with ``minimal``
                retention, which cannot satisfy the capture contract.
        """
        enabled = bool(runtime_config.get("runtime_capture_enable", False))
        quota = int(runtime_config.get("runtime_capture_disk_quota_bytes", DEFAULT_DISK_QUOTA_BYTES))
        retention = str(runtime_config.get("runtime_artifact_retention", "standard")).lower()
        if enabled and retention not in SUPPORTED_RETENTION:
            raise CaptureUnsupported(
                "runtime capture does not support runtime_artifact_retention="
                f"{retention!r}; use 'standard' or 'full' (retention='minimal' cannot "
                "guarantee a complete journal)"
            )
        return cls(enabled=enabled, quota_bytes=quota, retention=retention, route=route)

    def assert_route_supported(self) -> None:
        """Fail fast when the resolved route is outside the supported set.

        Raises:
            CaptureUnsupported: When the route is unknown. Capture never
                pretends a route is fully supported when it is not.
        """
        if not self.enabled:
            return
        if self.route is None:
            raise CaptureUnsupported(
                "runtime capture is enabled but no capture route was resolved; "
                "only the pinned minimax_m3 route is supported"
            )
        if self.route not in self.supported_routes:
            raise CaptureUnsupported(
                f"runtime capture does not support route {self.route!r}; "
                f"supported routes: {', '.join(self.supported_routes)}"
            )


@dataclass(frozen=True)
class CaptureAttempt:
    """An immutable numbered attempt directory inside a capture run."""

    index: int
    label: str
    path: Path

    @property
    def events_path(self) -> Path:
        return self.path / "events.jsonl"

    @property
    def blobs_path(self) -> Path:
        return self.path / "blobs"

    @property
    def stdout_path(self) -> Path:
        return self.path / "stdout.bin"

    @property
    def stderr_path(self) -> Path:
        return self.path / "stderr.bin"

    @property
    def opencode_config_path(self) -> Path:
        return self.path / "opencode.json"

    @property
    def db_snapshot_path(self) -> Path:
        return self.path / "opencode-db"

    def relative(self, root: Path) -> str:
        """Return this attempt's path relative to the capture root."""
        return str(self.path.relative_to(root))


@dataclass
class CaptureRun:
    """Run-local capture handle passed explicitly through the runtime flow.

    This object is intentionally **not** serialised into result artifacts and
    **not** stored on the executor. It owns only paths plus bookkeeping; journal
    writes go through :meth:`store_for_attempt`.
    """

    root: Path
    settings: CaptureSettings
    invocation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempt_counter: int = 0
    created_at: str = field(default_factory=lambda: _now_iso())
    attempts: List[CaptureAttempt] = field(default_factory=list)
    judges: List[CaptureAttempt] = field(default_factory=list)

    @classmethod
    def allocate(
        cls,
        results_outdir: Path,
        settings: CaptureSettings,
        test_id: str,
    ) -> "CaptureRun":
        """Allocate a fresh capture root under ``<results_outdir>/captures/``.

        Args:
            results_outdir: Executor results output directory.
            settings: Resolved capture settings.
            test_id: Test case id, recorded in the descriptor.

        Returns:
            A ``CaptureRun`` whose root already exists with private modes.

        Raises:
            CaptureUnsupported: If the resolved route is not supported.
        """
        settings.assert_route_supported()
        captures_root = ensure_private_dir(Path(results_outdir) / CAPTURES_DIRNAME)
        root = ensure_private_dir(captures_root / uuid.uuid4().hex)
        run = cls(root=root, settings=settings)
        write_private_json(
            root / "capture.json",
            {
                "invocation_id": run.invocation_id,
                "test_id": test_id,
                "created_at": run.created_at,
                "route": settings.route,
                "retention": settings.retention,
                "quota_bytes": settings.quota_bytes,
                "completeness": "in_progress",
            },
        )
        return run

    # ------------------------------------------------------------------
    # Attempt allocation
    # ------------------------------------------------------------------

    def next_attempt(self, label: str, judge: bool = False) -> CaptureAttempt:
        """Allocate the next immutable attempt directory.

        Args:
            label: Human-readable reason, e.g. ``initial``, ``retry:json_parse_failure``,
                ``review_nudge``. Stored verbatim in the descriptor.
            judge: Allocate under the separate ``judges/`` subtree so judge
                application-boundary captures never interleave with agent
                transport events.

        Returns:
            The freshly created :class:`CaptureAttempt`.
        """
        self.attempt_counter += 1
        subtree = "judges" if judge else ATTEMPTS_DIRNAME
        index = len(self.judges) + 1 if judge else len(self.attempts) + 1
        path = ensure_private_dir(self.root / subtree / f"attempt_{index:03d}")
        attempt = CaptureAttempt(index=index, label=label, path=path)
        (self.judges if judge else self.attempts).append(attempt)
        return attempt

    # ------------------------------------------------------------------
    # Descriptor
    # ------------------------------------------------------------------

    def descriptor(self, completion: str, reasons: List[str]) -> Dict[str, Any]:
        """Build the small ``capture.json`` payload describing this capture.

        The descriptor is intentionally small: it points at the archive and
        records why it ended, so a reader starting from the task artifact
        folder can navigate to the complete journal.
        """
        return {
            "invocation_id": self.invocation_id,
            "created_at": self.created_at,
            "route": self.settings.route,
            "retention": self.settings.retention,
            "quota_bytes": self.settings.quota_bytes,
            "completeness": completion,
            "reasons": list(reasons),
            "archive_path": str(self.root),
            "attempts": [
                {
                    "index": a.index,
                    "label": a.label,
                    "path": a.relative(self.root),
                    "events": a.relative(self.root) + "/events.jsonl",
                }
                for a in self.attempts
            ],
            "judge_attempts": [
                {
                    "index": a.index,
                    "label": a.label,
                    "path": a.relative(self.root),
                    "events": a.relative(self.root) + "/events.jsonl",
                }
                for a in self.judges
            ],
        }

    def finalize_descriptor(self, completion: str, reasons: List[str]) -> Optional[Path]:
        """Write the final ``capture.json``. Never raises."""
        try:
            return write_private_json(self.root / "capture.json", self.descriptor(completion, reasons))
        except Exception:
            return None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def reference_from_descriptor(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    """Build an independent pointer snapshot from one authoritative descriptor."""
    reference = {
        "capture_path": descriptor["archive_path"],
        "archive_path": descriptor["archive_path"],
        "invocation_id": descriptor["invocation_id"],
        "route": descriptor["route"],
        "retention": descriptor["retention"],
        "completeness": descriptor["completeness"],
        "reasons": descriptor["reasons"],
        "attempts": [a["path"] for a in descriptor["attempts"]],
        "judge_attempts": [a["path"] for a in descriptor["judge_attempts"]],
    }
    for key in ("descriptor_version", "transport_summary", "reconciliation"):
        if key in descriptor:
            reference[key] = descriptor[key]
    return copy.deepcopy(reference)
