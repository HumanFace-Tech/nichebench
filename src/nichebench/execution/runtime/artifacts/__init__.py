"""Runtime artifact handling package.

This package owns the runtime artifact lifecycle split out of the runtime
executor: redaction, validation artifact extraction, tool policy helpers,
catastrophic failure detection, and persistence to the results directory.

Boundary: no workspace/DDEV lifecycle, no trajectory reconstruction, and no
check execution logic lives here.
"""

from nichebench.execution.runtime.artifacts.failure_detection import (
    detect_catastrophic_failure,
)
from nichebench.execution.runtime.artifacts.persistence import save_runtime_artifacts
from nichebench.execution.runtime.artifacts.redaction import redact_artifact_payload
from nichebench.execution.runtime.artifacts.tool_policy import (
    build_tool_allowlist_check,
    extract_trajectory_tool_names,
    parse_rejected_tool_attempts,
)
from nichebench.execution.runtime.artifacts.validation import (
    extract_validation_artifacts,
)

__all__ = [
    "redact_artifact_payload",
    "extract_trajectory_tool_names",
    "parse_rejected_tool_attempts",
    "extract_validation_artifacts",
    "detect_catastrophic_failure",
    "build_tool_allowlist_check",
    "save_runtime_artifacts",
]
