"""Runtime artifact redaction helpers.

This module owns secret scrubbing for runtime artifacts only. It does not
persist files, classify failures, or interpret validation results.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Set

# Key substrings (after normalization) that indicate a secret value.
_SENSITIVE_KEYS: Set[str] = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "access_token",
    "openai_api_key",
    "groq_api_key",
    "anthropic_api_key",
}


def _redact_text(text: str) -> str:
    """Redact secret-like values from a text string.

    Uses explicit patterns for common credential formats so that keys embedded
    inside JSON strings (e.g. ``{"apiKey":"sk-..."}``) are also caught.
    """
    redacted = text
    patterns = [
        # Environment variable style: KEY=value
        r"(OPENAI_API_KEY\s*=\s*)[^\s\n]+",
        r"(GROQ_API_KEY\s*=\s*)[^\s\n]+",
        r"(ANTHROPIC_API_KEY\s*=\s*)[^\s\n]+",
        # Bearer token in plain text: Authorization: Bearer <token>
        r"(Authorization:\s*Bearer\s+)[^\s\n]+",
        # Bearer token in JSON: "Authorization":"Bearer <token>"
        r"([\"']Authorization[\"']?\s*[:=]\s*[\"']?Bearer\s+)[^\s\n,\"']+[\"']?",
        # API key in JSON: "apiKey":"value" or apiKey:"value" or apiKey=value
        r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s\n,\"']+[\"']?",
        # Token in JSON: "token":"value" or token:"value" or token=value
        r"(token[\"']?\s*[:=]\s*[\"']?)[^\s\n,\"']+[\"']?",
        # Password in JSON: "password":"value" or password:value
        r"(password[\"']?\s*[:=]\s*[\"']?)[^\s\n,\"']+[\"']?",
    ]
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted, flags=re.IGNORECASE)
    return redacted


def redact_artifact_payload(payload: Any) -> Any:
    """Recursively redact secret-like values from an artifact payload."""
    if payload is None:
        return None
    if isinstance(payload, str):
        return _redact_text(payload)
    if isinstance(payload, dict):
        output: Dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            normalized_key = key_str.lower().replace("-", "_")
            if normalized_key in _SENSITIVE_KEYS or any(
                marker in normalized_key
                for marker in ("api_key", "apikey", "authorization", "password", "secret", "token")
            ):
                output[key_str] = "[REDACTED]"
            else:
                output[key_str] = redact_artifact_payload(value)
        return output
    if isinstance(payload, list):
        return [redact_artifact_payload(item) for item in payload]
    return payload
