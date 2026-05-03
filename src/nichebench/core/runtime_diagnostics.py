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
    def __init__(self, test_id: str):
        self.test_id = test_id
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.ended_at: Optional[str] = None
        self.stages: List[Dict[str, Any]] = []
        self._open_stage: Optional[str] = None

    def stage_start(self, stage: str, evidence: Optional[Dict[str, Any]] = None) -> None:
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
    for stage in trace.get("stages", []):
        if stage.get("status") == "failed":
            return str(stage.get("stage"))
    return None
