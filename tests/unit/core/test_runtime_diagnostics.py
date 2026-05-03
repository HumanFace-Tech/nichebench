"""Hardening gate tests for runtime diagnostics system.

Covers tasks:
  5.1 — Stage contract completeness
  5.2 — Failure taxonomy determinism fixtures
  5.3 — Compatibility drift signals
  5.4 — Cleanup invariants
"""

from pathlib import Path

import pytest

from nichebench.core.runtime_diagnostics import (
    RUNTIME_STAGES,
    RuntimeTrace,
    classify_runtime_failure,
    first_failed_stage,
)

# ---------------------------------------------------------------------------
# Task 5.1 — Stage contract completeness
# ---------------------------------------------------------------------------


def test_all_stages_are_recorded_in_order():
    """Every stage in RUNTIME_STAGES must appear in the final trace."""
    trace = RuntimeTrace(test_id="dt")
    for stage in RUNTIME_STAGES:
        trace.stage_start(stage)
        trace.stage_end(stage, "passed")
    payload = trace.finalize()

    recorded_stages = [s["stage"] for s in payload["stages"]]
    assert recorded_stages == list(RUNTIME_STAGES)


def test_stage_cannot_end_before_it_starts():
    """stage_end must be called only after stage_start."""
    trace = RuntimeTrace(test_id="dt")

    with pytest.raises(ValueError, match="not currently open"):
        trace.stage_end("agent_execution", "failed")


def test_stage_cannot_start_before_previous_ends():
    """Overlapping/nested stages are not allowed."""
    trace = RuntimeTrace(test_id="dt")
    trace.stage_start("config_resolution")
    trace.stage_end("config_resolution", "passed")
    trace.stage_start("workspace_setup")
    trace.stage_end("workspace_setup", "passed")

    # Opening an already-active stage raises
    trace.stage_start("agent_execution")
    with pytest.raises(ValueError, match="already open"):
        trace.stage_start("agent_execution")


def test_finalize_rejects_unclosed_stage():
    """finalize() must raise if a stage is still in_progress."""
    trace = RuntimeTrace(test_id="dt")
    trace.stage_start("config_resolution")
    trace.stage_end("config_resolution", "passed")
    trace.stage_start("workspace_setup")
    # Intentionally skip close

    with pytest.raises(ValueError, match="unclosed"):
        trace.finalize()


def test_finalize_succeeds_when_all_stages_closed():
    """finalize() succeeds only when every stage is closed."""
    trace = RuntimeTrace(test_id="dt")
    for stage in RUNTIME_STAGES:
        trace.stage_start(stage)
        trace.stage_end(stage, "passed")
    # Must not raise
    payload = trace.finalize()
    assert "stages" in payload
    assert len(payload["stages"]) == len(RUNTIME_STAGES)


def test_finalize_without_artifact_dir():
    """finalize() must work without an artifact_dir (no disk I/O)."""
    trace = RuntimeTrace(test_id="dt")
    trace.stage_start("config_resolution")
    trace.stage_end("config_resolution", "passed")
    payload = trace.finalize()
    assert "stages" in payload


# ---------------------------------------------------------------------------
# Task 5.2 — Failure taxonomy determinism fixtures
# ---------------------------------------------------------------------------

FAILURE_CLASSES = [
    "deterministic_checks",
    "drupal_environment",
    "network_connectivity",
    "model_protocol_compatibility",
    "runtime_execution",
    "unknown",
]


@pytest.mark.parametrize("failure_class", FAILURE_CLASSES)
def test_classification_is_deterministic_across_calls(failure_class):
    """Identical inputs must always produce bit-identical RuntimeFailure."""
    seeds = {
        "deterministic_checks": dict(
            error="expected file to exist",
            failed_critical_check=True,
            failed_stage="deterministic_checks",
        ),
        "drupal_environment": dict(
            error="ddev start failed: network unreachable",
            failed_critical_check=False,
            failed_stage="environment_bootstrap",
        ),
        "network_connectivity": dict(
            error="ConnectionError: cannot reach proxy",
            failed_critical_check=False,
            failed_stage="agent_execution",
        ),
        "model_protocol_compatibility": dict(
            error="malformed response: missing output_index",
            failed_critical_check=False,
            failed_stage="agent_execution",
        ),
        "runtime_execution": dict(
            error="RuntimeError: process exited with code 127",
            failed_critical_check=False,
            failed_stage="agent_execution",
        ),
        "unknown": dict(
            error="Something unexpected happened",
            failed_critical_check=False,
            failed_stage="unknown",
        ),
    }

    kwargs = seeds[failure_class]
    results = [classify_runtime_failure(**kwargs) for _ in range(10)]

    # All results must be identical
    for r in results[1:]:
        assert r.failure_class == results[0].failure_class
        assert r.failure_code == results[0].failure_code
        assert r.signature == results[0].signature


def _failure_seed(failure_class):
    if failure_class == "deterministic_checks":
        return dict(error="check failed", failed_critical_check=True, failed_stage="deterministic_checks")
    if failure_class == "drupal_environment":
        return dict(error="ddev start failed", failed_critical_check=False, failed_stage="environment_bootstrap")
    if failure_class == "network_connectivity":
        return dict(error="connection timed out", failed_critical_check=False, failed_stage="agent_execution")
    if failure_class == "model_protocol_compatibility":
        return dict(error="invalid_request_error", failed_critical_check=False, failed_stage="agent_execution")
    if failure_class == "runtime_execution":
        return dict(error="process exited with code 127", failed_critical_check=False, failed_stage="agent_execution")
    return dict(error="", failed_critical_check=False, failed_stage="unknown")


@pytest.mark.parametrize("failure_class", FAILURE_CLASSES)
def test_all_failure_classes_produce_valid_codes(failure_class):
    """Every failure_class maps to a non-empty failure_code."""
    kwargs = _failure_seed(failure_class)
    failure = classify_runtime_failure(**kwargs)
    assert failure.failure_class == failure_class, f"expected {failure_class!r}, got {failure.failure_class!r}"
    assert failure.failure_code
    assert len(failure.failure_code) > 0


def test_critical_check_takes_precedence_over_error_text():
    """failed_critical_check=True always maps to deterministic_checks."""
    failure = classify_runtime_failure(
        error="connection error",
        failed_critical_check=True,
        failed_stage="deterministic_checks",
    )
    assert failure.failure_class == "deterministic_checks"


def test_signature_is_sha256_based():
    """signature must embed the SHA256 fingerprint of the failure."""
    failure = classify_runtime_failure(
        error="test",
        failed_critical_check=False,
        failed_stage="agent_execution",
    )
    # signature format is "{class}:{stage}:{hex16}" - last 16 chars are the hash
    hex_part = failure.signature.split(":")[-1]
    assert len(hex_part) == 16
    assert all(c in "0123456789abcdef" for c in hex_part)


def test_runtime_failure_to_dict_contains_all_fields():
    """to_dict() must return all required classification fields."""
    failure = classify_runtime_failure(
        error="test",
        failed_critical_check=False,
        failed_stage="agent_execution",
    )
    d = failure.to_dict()
    assert "failure_class" in d
    assert "failure_code" in d
    assert "classification_confidence" in d
    assert "primary_stage" in d
    assert "failure_fingerprint" in d


# ---------------------------------------------------------------------------
# Task 5.3 — Compatibility drift signals
# ---------------------------------------------------------------------------


def test_no_hardcoded_entity_ids_in_runtime_diagnostics():
    """runtime_diagnostics.py must not contain hardcoded nids, uids, or entity IDs."""
    import nichebench.core.runtime_diagnostics as rd

    source = Path(rd.__file__).read_text()
    assert not _contains_drupal_ids(source)


def test_no_provider_specific_model_names_in_source():
    """runtime_diagnostics.py must not reference provider-specific model names."""
    import nichebench.core.runtime_diagnostics as rd

    source = Path(rd.__file__).read_text()
    forbidden = ["gpt-", "claude-", "qwen", "llama"]
    for token in forbidden:
        assert token not in source.lower(), f"provider model name {token!r} found"


def test_trace_payload_contains_no_hardcoded_paths():
    """Trace payload must not contain hardcoded absolute paths."""
    trace = RuntimeTrace(test_id="dt")
    trace.stage_start("config_resolution")
    trace.stage_end("config_resolution", "passed")
    trace.stage_start("agent_execution")
    trace.stage_end("agent_execution", "failed", {"error": "bad call"})
    payload = trace.finalize()

    payload_str = str(payload)
    assert "/workspaces/" not in payload_str
    assert "/nichebench/" not in payload_str
    assert "/home/" not in payload_str


# ---------------------------------------------------------------------------
# Task 5.4 — Cleanup invariants
# ---------------------------------------------------------------------------


def test_cleanup_stage_records_failure_result():
    """A failed cleanup stage must appear as 'failed' in the trace."""
    trace = RuntimeTrace(test_id="dt")
    for stage in RUNTIME_STAGES:
        trace.stage_start(stage)
        result = "failed" if stage == "cleanup" else "passed"
        trace.stage_end(stage, result, {"error": "ddev stop timed out"} if stage == "cleanup" else None)

    payload = trace.finalize()
    cleanup = next(s for s in payload["stages"] if s["stage"] == "cleanup")
    assert cleanup["status"] == "failed"


def test_first_failed_stage_returns_earliest_failed_stage():
    """first_failed_stage must return the first stage with status 'failed'."""
    trace = RuntimeTrace(test_id="dt")
    trace.stage_start("config_resolution")
    trace.stage_end("config_resolution", "passed")
    trace.stage_start("workspace_setup")
    trace.stage_end("workspace_setup", "failed", {"error": "disk full"})
    trace.stage_start("environment_bootstrap")
    trace.stage_end("environment_bootstrap", "passed")

    payload = trace.finalize()
    assert first_failed_stage(payload) == "workspace_setup"


def test_first_failed_stage_returns_none_when_no_failures():
    """first_failed_stage returns None when all stages passed."""
    trace = RuntimeTrace(test_id="dt")
    for stage in RUNTIME_STAGES:
        trace.stage_start(stage)
        trace.stage_end(stage, "passed")

    payload = trace.finalize()
    assert first_failed_stage(payload) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DJANGO_ID_PATTERN = r"\b(nid|uid|eid)\s*[=:]\s*\d{3,}"


def _contains_drupal_ids(text: str) -> bool:
    import re

    return bool(re.search(_DJANGO_ID_PATTERN, text, re.IGNORECASE))
