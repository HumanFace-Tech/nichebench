"""Runtime artifact redaction helpers.

This module owns secret scrubbing for runtime artifacts only. It does not
persist files, classify failures, or interpret validation results.
"""

from __future__ import annotations

from typing import Any

from nichebench.execution.runtime.capture.scrub import REDACTED, is_credential_value, scrub_text_formats

_REDACTED = REDACTED

# Backwards-compatible alias for callers that imported the old constant.
SENSITIVE_KEYS: frozenset[str] = frozenset({"api_key", "apikey", "authorization", "password", "secret", "token"})


def _redact_text(text: str) -> str:
    """Redact credential-formatted literals from a text string.

    Delegates to the shared capture scrubber so runtime artifacts and capture
    payloads use one policy. Only real credential *formats* are matched; a word
    like ``token`` in ordinary content, and any numeric usage or config value,
    survive untouched.
    """
    scrubbed, _hits = scrub_text_formats(text)
    return scrubbed


def redact_artifact_payload(payload: Any) -> Any:
    """Recursively redact credential values from an artifact payload.

    Key-name driven and format driven only. Numeric usage counters, token
    counts, and configuration keys are ordinary data and must survive so the
    archive stays usable for training and analysis.
    """
    if payload is None:
        return None
    if isinstance(payload, str):
        return _redact_text(payload)
    if isinstance(payload, dict):
        output: dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            if is_credential_value(key_str, value):
                output[key_str] = _REDACTED
            else:
                output[key_str] = redact_artifact_payload(value)
        return output
    if isinstance(payload, list):
        return [redact_artifact_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_artifact_payload(item) for item in payload)
    return payload
