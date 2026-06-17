"""Runtime artifact persistence for result bundles.

This module owns filesystem writes for runtime artifacts only. It does not
redact data directly beyond delegating to the redaction helper, and it does not
interpret checks or detect failures.
"""

from __future__ import annotations

import json
from typing import Any

from nichebench.execution.runtime.artifacts.redaction import redact_artifact_payload


def save_runtime_artifacts(
    result: Any,
    results_outdir: Any,
    evaluation_config: Any,
    redact_func: Any = None,
) -> None:
    """Persist runtime artifacts from a TestResult to the results directory.

    This function is normally called by ``RuntimeExecutionMixin._save_runtime_artifacts``
    but can also be called directly with the appropriate parameters.

    Args:
        result: A ``TestResult`` (or duck-typed equivalent) with a
            ``runtime_artifacts`` dict attribute.
        results_outdir: ``Path`` to the harness results root directory.
        evaluation_config: Runtime configuration dict; controls artifact retention
            policy via ``runtime_artifact_retention`` key.
        redact_func: Callable to redact secrets from artifact payloads before writing.
            Defaults to ``redact_artifact_payload``.
    """
    if redact_func is None:
        redact_func = redact_artifact_payload

    artifacts = getattr(result, "runtime_artifacts", None)
    if not artifacts:
        return

    retention = str(evaluation_config.get("runtime_artifact_retention", "standard")).lower()

    # Determine output path
    test_id = getattr(result, "test_case", None)
    test_id = getattr(test_id, "id", str(test_id)) if test_id else "unknown"
    trials_total = getattr(result, "trials_total", 1)
    trial = getattr(result, "trial", 1)

    if trials_total > 1:
        outdir = results_outdir / "runtime" / test_id / f"trial_{trial}"
    else:
        outdir = results_outdir / "runtime" / test_id

    outdir.mkdir(parents=True, exist_ok=True)

    # --- Trajectory ---
    if "trajectory.json" in artifacts and retention in ("standard", "full"):
        trajectory = artifacts["trajectory.json"]
        if trajectory:
            trajectory = redact_func(trajectory)
            (outdir / "trajectory.json").write_text(json.dumps(trajectory, indent=2), encoding="utf-8")

    # --- Metadata ---
    if "metadata.json" in artifacts:
        metadata = artifacts["metadata.json"]
        if metadata:
            if trials_total > 1:
                metadata = dict(metadata)
                metadata["trial"] = trial
                metadata["trials_total"] = trials_total
            metadata = redact_func(metadata)
            (outdir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # --- Runtime trace ---
    if "runtime_trace.json" in artifacts:
        runtime_trace = artifacts["runtime_trace.json"]
        if runtime_trace:
            runtime_trace = redact_func(runtime_trace)
            (outdir / "runtime_trace.json").write_text(json.dumps(runtime_trace, indent=2), encoding="utf-8")

    if retention == "minimal":
        return

    # --- Run log ---
    if "run.log" in artifacts and retention in ("standard", "full"):
        run_log = artifacts["run.log"]
        if run_log:
            (outdir / "run.log").write_text(redact_func(str(run_log)), encoding="utf-8")

    # --- Checks ---
    if "checks.json" in artifacts and retention in ("standard", "full"):
        checks = artifacts["checks.json"]
        if checks:
            checks = redact_func(checks)
            (outdir / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")

    # --- Validation diagnostics ---
    for artifact_name in ("last_phpcs.txt", "last_phpstan.txt", "watchdog_errors.txt"):
        if artifact_name in artifacts and retention in ("standard", "full"):
            artifact_text = artifacts[artifact_name]
            if artifact_text:
                (outdir / artifact_name).write_text(
                    redact_func(str(artifact_text)),
                    encoding="utf-8",
                )

    # --- Final diff ---
    if "final.diff" in artifacts and retention in ("standard", "full"):
        final_diff = artifacts["final.diff"]
        if final_diff:
            (outdir / "final.diff").write_text(redact_func(str(final_diff)), encoding="utf-8")

    # --- Partial trajectory (timeout) ---
    if "opencode_partial_trajectory.json" in artifacts and retention in ("standard", "full"):
        partial_trajectory = artifacts["opencode_partial_trajectory.json"]
        if partial_trajectory:
            partial_trajectory = redact_func(partial_trajectory)
            (outdir / "opencode_partial_trajectory.json").write_text(
                json.dumps(partial_trajectory, indent=2),
                encoding="utf-8",
            )

    # --- Session dump ---
    if "opencode_session_dump.json" in artifacts and retention in ("standard", "full"):
        session_dump = artifacts["opencode_session_dump.json"]
        if session_dump:
            session_dump = redact_func(session_dump)
            (outdir / "opencode_session_dump.json").write_text(
                json.dumps(session_dump, indent=2),
                encoding="utf-8",
            )
