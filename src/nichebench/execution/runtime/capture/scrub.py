"""Capture payload scrubbing.

Three layers, and the differences matter:

1. **Credential-key scrubbing** (:func:`redact_credential_keys`) — structural.
   A value stored under a key whose *name* means "credential" is replaced with
   ``[REDACTED]``. ``token`` counts: a bare ``token`` key holds a credential,
   while ``token_count`` / ``input_tokens`` / ``max_tokens`` are metrics and
   survive untouched.

2. **Exact-credential scrubbing** (:func:`scrub_known_credentials`) — literal.
   Only byte sequences the harness actually holds in memory (provider API keys,
   proxy capability tokens) are replaced. Each hit is recorded so the caller can
   mark the affected artifact as *not* an exact byte copy.

3. **Streaming scrubbing** (:class:`StreamingCredentialScrubber`) — the same
   literal replacement applied across a chunked byte stream, holding back enough
   tail that a credential split across two chunks is still caught.

Explicitly **not** scrubbed: header names, numeric usage values, or content the
harness does not know the value of. Blanket redaction destroys the signal the
archive exists to preserve, so an *unknown* secret is not speculatively removed
— it is reported as an explicit exclusion instead.

Ownership: dependency-free leaf module.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Set, Tuple

REDACTED = "[REDACTED]"

# Keys whose *exact* normalized name is a credential.
_CREDENTIAL_KEYS: Set[str] = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer_token",
    "id_token",
    "private_key",
    "session_key",
    "x_api_key",
    "bearer",
    "set_cookie",
    "cookie",
    "openai_api_key",
    "groq_api_key",
    "anthropic_api_key",
    "minimax_api_key",
    "xai_api_key",
    "openrouter_api_key",
}

# Substrings that make a longer key name a credential regardless of shape.
_CREDENTIAL_KEY_MARKERS: Tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "private_key",
)

# Suffixes that turn a credential-looking word into an ordinary metric.
_METRIC_SUFFIXES: Tuple[str, ...] = ("_count", "_tokens", "_token", "_usage", "_ids")

_CREDENTIAL_ENV_NAMES = "OPENAI|GROQ|ANTHROPIC|MINIMAX|XAI|OPENROUTER|DEEPSEEK|TOGETHERAI|MISTRAL|AZURE"

# Literal credential shapes scrubbed from free text. These are *formats*, not
# heuristics on the word "token".
_TEXT_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(rf"((?:{_CREDENTIAL_ENV_NAMES})_[A-Z0-9_]*(?:KEY|TOKEN|SECRET)\s*=\s*)(?![^\s]*(?:\(|\[))\S+"),
    re.compile(r"(\b[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|SECRET_KEY|AUTH_TOKEN)\s*=\s*)(?![^\s]*(?:\(|\[))\S+"),
    re.compile(
        r"((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret[_-]?key)\s*[:=]\s*)" r"(?![^\s]*(?:\(|\[))\S+",
        re.IGNORECASE,
    ),
    # Bearer in plain text or JSON, quoted or bare.
    re.compile(r"((?:[\"']?authorization[\"']?\s*[:=]\s*)?[\"']?Bearer\s+)[^\s\"',}]+", re.IGNORECASE),
    re.compile(r"((?:[\"']?x-api-key[\"']?\s*[:=]\s*)[\"']?)[^\s\"',}]+", re.IGNORECASE),
    # Quoted-JSON credential values: the closing quote must be consumed by the
    # match so the result stays valid JSON rather than eating the separator.
    re.compile(
        rf"([\"'](?:(?:(?:{_CREDENTIAL_ENV_NAMES})_)?api[_-]?key|access[_-]?token|refresh[_-]?token"
        r"|secret|token|authorization|password)"
        r"[\"']\s*:\s*[\"'])[^\"']+",
        re.IGNORECASE,
    ),
    re.compile(r"(\"password\"\s*:\s*\")[^\"]+"),
)


def normalize_key(key: str) -> str:
    """Normalize a mapping key for credential-name matching."""
    return str(key).lower().replace("-", "_").replace(" ", "_")


def is_credential_key(key: str) -> bool:
    """Return True when ``key`` names a credential rather than ordinary data.

    ``token`` is a credential; ``token_count``, ``input_tokens``, and
    ``max_tokens`` are metrics and are not.
    """
    normalized = normalize_key(key)
    if normalized in _CREDENTIAL_KEYS:
        return True
    if any(normalized.endswith(suffix) for suffix in _METRIC_SUFFIXES) and normalized not in _CREDENTIAL_KEYS:
        return False
    return any(marker in normalized for marker in _CREDENTIAL_KEY_MARKERS)


def redact_credential_keys(payload: Any) -> Any:
    """Recursively replace values stored under credential-named keys."""
    if isinstance(payload, dict):
        output: Dict[str, Any] = {}
        for key, value in payload.items():
            if is_credential_value(str(key), value):
                output[str(key)] = REDACTED
            else:
                output[str(key)] = redact_credential_keys(value)
        return output
    if isinstance(payload, (list, tuple)):
        return [redact_credential_keys(item) for item in payload]
    return payload


def is_credential_value(key: str, value: Any) -> bool:
    """Preserve numeric token IDs, not numeric passwords or other credentials."""
    return is_credential_key(key) and not (normalize_key(key) == "token" and isinstance(value, (int, float, bool)))


def normalise_known(known: Iterable[str]) -> List[str]:
    """Return usable literal credentials, longest first.

    Longest-first matters: a short credential that happens to be a prefix of a
    longer one must not be replaced first, or the longer value would be left
    half-intact.
    """
    return sorted({c for c in known if c}, key=len, reverse=True)


def scrub_known_credentials(text: str, known: Iterable[str]) -> Tuple[str, List[str]]:
    """Replace every literal occurrence of a known credential in ``text``.

    Returns:
        Tuple of (scrubbed_text, hits) where ``hits`` lists the credential
        values that were actually replaced. An empty list means the text is
        byte-exact with respect to known credentials.
    """
    if not text:
        return text, []
    hits: List[str] = []
    for credential in normalise_known(known):
        if credential in text:
            text = text.replace(credential, REDACTED)
            hits.append(credential)
    return text, hits


def scrub_bytes(data: bytes, known: Iterable[str]) -> Tuple[bytes, List[str]]:
    """Replace literal known credentials in raw bytes."""
    hits: List[str] = []
    out = data
    for credential in normalise_known(known):
        needle = credential.encode("utf-8", errors="ignore")
        if needle and needle in out:
            out = out.replace(needle, REDACTED.encode("utf-8"))
            hits.append(credential)
    return out, hits


def scrub_text_formats(text: str) -> Tuple[str, List[str]]:
    """Scrub credential-formatted literals from free text.

    Returns:
        Tuple of (scrubbed_text, hits). ``hits`` holds pattern labels, never the
        secret itself.
    """
    if not text:
        return text, []
    hits: List[str] = []
    scrubbed = text
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        structural = redact_credential_keys(parsed)
        if structural != parsed:
            scrubbed = json.dumps(structural, ensure_ascii=False)
            hits.append("credential_key")
    for pattern in _TEXT_PATTERNS:
        replaced, count = pattern.subn(r"\1" + REDACTED, scrubbed)
        if count:
            hits.append(pattern.pattern)
            scrubbed = replaced
    return scrubbed, hits


def scrub_mapping_known_credentials(
    payload: Mapping[str, Any],
    known: Iterable[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """Scrub known credentials from string leaves of a mapping.

    Credential-named keys are also redacted structurally, so a captured header
    mapping never retains an ``Authorization`` value even when the harness did not
    know its literal value.
    """
    hits: List[str] = []

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: Dict[str, Any] = {}
            for key, value in node.items():
                if is_credential_value(str(key), value):
                    out[str(key)] = REDACTED
                else:
                    out[str(key)] = _walk(value)
            return out
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if isinstance(node, str):
            scrubbed, found = scrub_known_credentials(node, known)
            hits.extend(found)
            scrubbed, formats = scrub_text_formats(scrubbed)
            hits.extend(formats)
            return scrubbed
        return node

    result = _walk(payload)
    return result, sorted(set(hits))


class StreamingCredentialScrubber:
    """Scrub literal known credentials across a chunked byte stream.

    A credential can straddle two reads — an SSE frame boundary, a pipe buffer,
    a socket segment — so scrubbing each chunk independently would miss it. This
    holds back the longest possible partial match and releases it on the next
    feed, or on :meth:`flush` at end of stream.

    Only the *stored* copy is transformed. Bytes forwarded to the agent or to
    the provider are never rewritten.
    """

    def __init__(self, known: Iterable[str]) -> None:
        self._credentials = normalise_known(known)
        needles = [c.encode("utf-8") for c in self._credentials]
        self._pattern = re.compile(b"|".join(re.escape(c) for c in needles)) if needles else None
        self._needles = needles
        self._carry = b""
        self._redacted = False

    @property
    def redacted(self) -> bool:
        """True once anything has been replaced in this stream."""
        return self._redacted

    def feed(self, data: bytes) -> bytes:
        """Scrub a chunk and return the storable bytes for it.

        The tail that could still be the start of a credential is withheld and
        returned by the next call (or by :meth:`flush`).
        """
        if not self._credentials:
            return data
        buffer = self._carry + data
        # Search the whole buffer, not a head sliced before searching: slicing
        # first cuts a complete credential at the release boundary and leaks it.
        # Retain only a suffix that is actually a credential prefix. Ordinary
        # small SSE frames can therefore be durably stored immediately.
        hold = 0
        for needle in self._needles:
            for size in range(min(len(needle) - 1, len(buffer)), hold, -1):
                if buffer.endswith(needle[:size]):
                    hold = size
                    break
        release = len(buffer) - hold
        cursor = 0
        output = bytearray()
        assert self._pattern is not None
        for match in self._pattern.finditer(buffer):
            if match.start() >= release:
                break
            output.extend(buffer[cursor : match.start()])
            output.extend(REDACTED.encode("utf-8"))
            self._redacted = True
            cursor = match.end()
            release = max(release, cursor)
        output.extend(buffer[cursor:release])
        self._carry = buffer[release:]
        return bytes(output)

    def flush(self) -> bytes:
        """Scrub and release the withheld tail. Returns bytes when non-empty."""
        if not self._carry:
            return b""
        tail, self._carry = self._carry, b""
        scrubbed, hits = scrub_bytes(tail, self._credentials)
        if hits:
            self._redacted = True
        return scrubbed


def redact_payload_with_known(
    payload: Any,
    known: Iterable[str],
) -> Tuple[Any, List[str]]:
    """Scrub both credential-named keys and literal known values from a payload."""
    structural = redact_credential_keys(payload)
    return scrub_mapping_known_credentials(structural, known)
