"""Trace module — in-flight stage tracing and runtime failure classification.

These symbols were previously in ``core/runtime_diagnostics.py``.
They are used by the runtime execution harness to emit structured stage traces
and to classify failures after the fact.

Stage tracing
-------------
``RuntimeTrace`` records a ordered list of stages (see ``RUNTIME_STAGES``) with
in/out timestamps and optional evidence dicts.  It is serialised to
``runtime_trace.json`` as part of the artifact bundle.

Failure classification
---------------------
``classify_runtime_failure`` inspects an error message and runtime state to
produce a ``RuntimeFailure`` with:
  - ``failure_class`` — high-level category (e.g. ``drupal_environment``)
  - ``failure_code`` — specific error code (e.g. ``drupal.env_command_failed``)
  - ``confidence`` — classification confidence in 0–1
  - ``signature`` — SHA-256 fingerprint for stability/clustering

How runtime trace is meant to be consumed
----------------------------------------
- ``RuntimeTrace.finalize()`` output is written to ``runtime_trace.json`` in
  the trial result directory.
- ``forensics.collect_reports`` reads ``runtime_trace.json`` to populate
  ``first_failed_stage`` and timing fields in the forensics report.
- ``classify_runtime_failure`` is called by the orchestrator after a run ends
  and its output is stored in ``metadata.json`` for later analysis.
- ``first_failed_stage`` is a utility that scans a serialised trace for the
  first stage marked ``failed``; it does not perform classification.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

RUNTIME_STAGES: tuple[str, ...] = (
    "config_resolution",
    "workspace_setup",
    "environment_bootstrap",
    "agent_execution",
    "deterministic_checks",
    "judge_scoring",
    "artifact_finalization",
    "cleanup",
)


@dataclass
class RuntimeFailure:
    """A classified runtime failure with a stability signature."""

    failure_class: str
    failure_code: str
    confidence: float
    primary_stage: str
    signature: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "failure_code": self.failure_code,
            "classification_confidence": self.confidence,
            "primary_stage": self.primary_stage,
            "failure_fingerprint": self.signature,
        }


class RuntimeTrace:
    """Tracks in-progress runtime stages for a single trial.

    Usage::

        trace = RuntimeTrace("drupal_runtime_001")
        trace.stage_start("workspace_setup")
        # ... do work ...
        trace.stage_end("workspace_setup", "success")
        trace.finalize()   # returns serialisable dict
    """

    def __init__(self, test_id: str):
        self.test_id = test_id
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.ended_at: Optional[str] = None
        self.stages: List[Dict[str, Any]] = []
        self.capture_ref: Dict[str, Any] = {}
        self._open_stage: Optional[str] = None

    def stage_start(self, stage: str, evidence: Optional[Dict[str, Any]] = None) -> None:
        """Mark the start of a named stage."""
        if self._open_stage is not None:
            raise ValueError(f"cannot start stage {stage!r} — stage {self._open_stage!r} is already open")
        self.stages.append(
            {
                "stage": stage,
                "status": "in_progress",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": None,
                "evidence": evidence or {},
            }
        )
        self._open_stage = stage

    def stage_end(self, stage: str, status: str, evidence: Optional[Dict[str, Any]] = None) -> None:
        """Mark the end of a named stage with a terminal status."""
        if self._open_stage != stage:
            raise ValueError(f"stage {stage!r} is not currently open")
        for item in reversed(self.stages):
            if item.get("stage") == stage and item.get("status") == "in_progress":
                item["status"] = status
                item["ended_at"] = datetime.now(timezone.utc).isoformat()
                if evidence:
                    merged = dict(item.get("evidence") or {})
                    merged.update(evidence)
                    item["evidence"] = merged
                self._open_stage = None
                return
        self._open_stage = None

    def attach_capture(self, capture_ref: Dict[str, Any]) -> None:
        """Attach a capture descriptor reference to the trace payload.

        Only the reference travels here — the archive itself stays on disk and is
        never inlined into ``runtime_trace.json`` or ``details.jsonl``.
        """
        self.capture_ref = dict(capture_ref)

    def finalize(self) -> Dict[str, Any]:
        """Return a serialisable snapshot of the completed trace."""
        if self._open_stage is not None:
            raise ValueError(f"finalize() called with unclosed stage {self._open_stage!r}")
        self.ended_at = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "test_id": self.test_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "stages": self.stages,
        }
        if self.capture_ref:
            payload["capture"] = self.capture_ref
        return payload


def classify_runtime_failure(error: Optional[str], failed_critical_check: bool, failed_stage: str) -> RuntimeFailure:
    """Classify a runtime failure and produce a stable fingerprint.

    The fingerprint is a SHA-256 hash of the concatenated failure attributes,
    truncated to 16 hex characters.
    """
    text = (error or "").lower()
    failure_class = "unknown"
    failure_code = "unknown.error"
    confidence = 0.5

    if failed_critical_check:
        failure_class = "deterministic_checks"
        failure_code = "checks.critical_failed"
        confidence = 0.9
    elif "ddev" in text or "drush" in text:
        failure_class = "drupal_environment"
        failure_code = "drupal.env_command_failed"
        confidence = 0.9
    elif "[watchdog:stop-idle]" in text:
        failure_class = "agent_execution"
        failure_code = "agent.did_not_exit"
        confidence = 0.95
    elif "[watchdog:inactivity]" in text:
        failure_class = "agent_execution"
        failure_code = "agent.execution_stalled"
        confidence = 0.95
    elif failed_stage == "agent_execution" and "timed out" in text and "connection" not in text:
        failure_class = "agent_execution"
        failure_code = "agent.execution_timeout"
        confidence = 0.9
    elif "timed out" in text or "connection" in text or "network" in text:
        failure_class = "network_connectivity"
        failure_code = "network.request_failed"
        confidence = 0.85
    elif "model not found" in text or "invalid_request_error" in text or "tool" in text:
        failure_class = "model_protocol_compatibility"
        failure_code = "model.protocol_mismatch"
        confidence = 0.8
    elif text:
        failure_class = "runtime_execution"
        failure_code = "runtime.execution_error"
        confidence = 0.7

    normalized = re.sub(r"\s+", " ", text).strip()
    base = f"{failure_class}|{failure_code}|{failed_stage}|{normalized[:300]}"
    signature = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return RuntimeFailure(
        failure_class=failure_class,
        failure_code=failure_code,
        confidence=confidence,
        primary_stage=failed_stage,
        signature=f"{failure_class}:{failed_stage}:{signature}",
    )


def first_failed_stage(trace: Dict[str, Any]) -> Optional[str]:
    """Return the name of the first stage with status 'failed', or None."""
    for stage in trace.get("stages", []):
        if stage.get("status") == "failed":
            return str(stage.get("stage"))
    return None


# Runtime failures caused by the harness/environment rather than by the model
# under test.  These must not be scored as model failures.
#
# ``model_protocol_compatibility`` is deliberately absent: the model itself
# does not speak the expected protocol, so that failure belongs to the model
# and stays in the model score.
_INFRASTRUCTURE_FAILURE_CLASSES = frozenset({"drupal_environment", "network_connectivity"})

# Stages that run before the model acts.  A failure in one of them is
# harness-side by construction — the model never produced work — so it must
# not be scored as a model failure.  ``deterministic_checks`` is deliberately
# absent: a failed critical check is a model failure and stays scored.
_INFRASTRUCTURE_SETUP_STAGES = frozenset(
    {
        "config_resolution",
        "workspace_setup",
        "environment_bootstrap",
        "image_build",
        "artifact_finalization",
    }
)

# Substrings that identify a known harness/configuration problem in the
# error text (missing image, missing API key, unusable Docker daemon, ...).
_INFRASTRUCTURE_ERROR_MARKERS = (
    "unrecognized flag",
    "unknown flag",
    "runtime_container_image must be configured",
    "cannot connect to the docker daemon",
    "no such file or directory: 'docker'",
    "docker: not found",
    "api key missing",
    "missing api key",
    "pull access denied",
    "unauthorized",
    "image not found",
    "no such image",
    "failed to build",
    "error getting credentials",
    "permission denied",
    "image build lock",
    "ddev start lock",
)


def is_infrastructure_failure(
    failure_class: str,
    error: Optional[str],
    failed_stage: Optional[str] = None,
) -> bool:
    """Return True when a runtime failure is harness/environment infrastructure.

    A model-quality score must not be dragged down by a broken cage image, a
    missing Docker credential, or an unsupported CLI flag.

    Args:
        failure_class: The classified failure class (see
            :func:`classify_runtime_failure`).
        error: The raw error text of the failure.
        failed_stage: Optional name of the runtime stage that failed.  A
            failure in a setup stage (see ``_INFRASTRUCTURE_SETUP_STAGES``) is
            harness-side by construction, even when the error text carries no
            known marker.

    Returns:
        True when the failure must be excluded from model-quality scores.
    """
    stage = (failed_stage or "").strip().lower()
    if stage in _INFRASTRUCTURE_SETUP_STAGES:
        # Setup stages run before the model acts; nothing the model did can
        # have caused the failure.
        return True
    text = (error or "").lower()
    if any(marker in text for marker in _INFRASTRUCTURE_ERROR_MARKERS):
        # A known harness/configuration marker is decisive: a broken image, a
        # missing credential or an unsupported CLI is never a model-quality
        # problem.  The marker wins over the (loose) model-protocol heuristic,
        # which also fires on ordinary authentication errors.
        return True
    # ``model_protocol_compatibility`` is not part of the infrastructure class
    # set: a model that does not speak the expected protocol is a model-side
    # failure and stays in the model score.
    return failure_class in _INFRASTRUCTURE_FAILURE_CLASSES
