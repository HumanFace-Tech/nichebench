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

    def finalize(self) -> Dict[str, Any]:
        """Return a serialisable snapshot of the completed trace."""
        if self._open_stage is not None:
            raise ValueError(f"finalize() called with unclosed stage {self._open_stage!r}")
        self.ended_at = datetime.now(timezone.utc).isoformat()
        return {
            "test_id": self.test_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "stages": self.stages,
        }


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
