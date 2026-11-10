"""Structural sanity checks for code-generation model outputs.

This layer is deliberately *lean*: it fails a response outright only when
there is no code at all or when the response is obviously truncated.
Missing declared paths, duplicate paths and language parse complaints are
advisory — they are attached to the judge output and never lower the score.

Expected response markers
=========================
``FILENAME: path``, ``PATCH: path`` and ``REPLACE: path`` lines each
introduce a fenced code block; generic fenced blocks (```` ```lang ... ``` ````)
are captured too without assuming a path.  Narration before, between and
around blocks is tolerated.

What this module never does
===========================
No class-name, namespace, architecture or Drupal-semantic inspection — that
belongs to the judge.  Standard library plus the already-required ``pyyaml``
only; no network, no workspace, no DDEV.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from nichebench.metrics.types import SanityFinding

# Declared-file markers, recognised case-insensitively on their own line.
_MARKER_RE = re.compile(r"^\s*(FILENAME|PATCH|REPLACE)\s*:\s*(\S+)\s*$", re.IGNORECASE)
_KIND_BY_MARKER = {"FILENAME": "filename", "PATCH": "patch", "REPLACE": "replace"}
_FENCE_PREFIX = "```"
_TRUNCATION_MARKER = "[truncated]"
_MAX_EVIDENCE = 200


@dataclass(frozen=True)
class CodeBlock:
    """One fenced code block extracted from a model response.

    ``kind`` records how the block was introduced: ``filename`` /
    ``patch`` / ``replace`` for a ``FILENAME:`` / ``PATCH:`` /
    ``REPLACE:`` marker line, or ``fence`` for a generic fenced block
    (``path`` is ``None`` then).
    """

    path: str | None
    kind: str
    content: str


@dataclass(frozen=True)
class _ParsedResponse:
    """Internal parse result: blocks plus fence/marker bookkeeping."""

    # Each block is paired with the info string of its opening fence.
    blocks: list[tuple[CodeBlock, str]]
    marker_paths: list[str]
    fence_count: int
    open_fence: bool


def _parse(response: str) -> _ParsedResponse:
    """Scan ``response`` line by line into blocks, markers and fence state.

    A marker line sets the path for the *next* fenced block (narration in
    between is tolerated).  Fence lines toggle open/close, so an odd number
    of them leaves ``open_fence`` set — the truncation signal.
    """
    blocks: list[tuple[CodeBlock, str]] = []
    marker_paths: list[str] = []
    pending: tuple[str, str] | None = None
    fence_count = 0
    open_fence = False
    info = ""
    lines: list[str] = []

    def _append_block() -> None:
        nonlocal pending
        content = "\n".join(lines)
        if pending is None:
            blocks.append((CodeBlock(path=None, kind="fence", content=content), info))
            return
        kind, path = pending
        blocks.append((CodeBlock(path=path, kind=kind, content=content), info))
        pending = None

    for line in response.splitlines():
        stripped = line.strip()

        if open_fence:
            if not stripped.startswith(_FENCE_PREFIX):
                lines.append(line)
                continue
            _append_block()
            open_fence, info, lines = False, "", []
            continue

        marker = _MARKER_RE.match(line)
        if marker is not None:
            pending = (_KIND_BY_MARKER[marker.group(1).upper()], marker.group(2))
            marker_paths.append(marker.group(2))
            continue

        if stripped.startswith(_FENCE_PREFIX):
            fence_count += 1
            open_fence = True
            info = stripped[len(_FENCE_PREFIX) :].strip()
            lines = []

    if open_fence:
        # Truncated response: keep the partial block, the caller flags it.
        _append_block()

    return _ParsedResponse(
        blocks=blocks,
        marker_paths=marker_paths,
        fence_count=fence_count,
        open_fence=open_fence,
    )


def extract_blocks(response: str) -> list[CodeBlock]:
    """Return every fenced code block found in ``response``.

    ``FILENAME:`` / ``PATCH:`` / ``REPLACE:`` markers attach their path and
    kind to the block that follows; generic fenced blocks come back with
    ``path=None`` and ``kind="fence"``.  Narration around the blocks is
    ignored.
    """
    return [block for block, _ in _parse(response).blocks]


def _snippet(text: str) -> str:
    """Evidence snippet: stripped text capped at ``_MAX_EVIDENCE`` chars."""
    return text.strip()[:_MAX_EVIDENCE]


def _first_line(text: str) -> str:
    """First non-empty line of ``text``, or ``"unknown error"``."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else "unknown error"


def _looks_like_diff(content: str) -> bool:
    """Return ``True`` when the first non-empty line looks like a diff."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.startswith(("diff --git", "--- ", "+++ ", "@@"))
    return False


def _looks_like_json(content: str) -> bool:
    """Return ``True`` when the block starts like a JSON document."""
    return content.lstrip()[:1] in ("{", "[")


def _php_lint(block: CodeBlock) -> list[SanityFinding]:
    """Run ``php -l`` on a temporary copy of ``block``.

    A syntax error is advisory; a lint that cannot run at all (missing
    binary, timeout, unreadable temp file) is recorded as ``skipped`` —
    neither ever fails the task on its own.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="nichebench-sanity-") as tmpdir:
            php_file = Path(tmpdir) / "snippet.php"
            php_file.write_text(block.content, encoding="utf-8")
            result = subprocess.run(
                ["php", "-l", str(php_file)],
                capture_output=True,
                text=True,
                timeout=30,
            )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return [
            SanityFinding(
                check="sanity_php_skipped",
                severity="skipped",
                message=f"php -l could not run: {_first_line(str(exc))}",
                evidence=block.path or "",
            )
        ]

    if result.returncode == 0:
        return []
    detail = _first_line(result.stderr or result.stdout or "")[:_MAX_EVIDENCE]
    return [
        SanityFinding(
            check="sanity_php_syntax",
            severity="advisory",
            message=f"php -l reported a syntax error: {detail}",
            evidence=block.path or "",
        )
    ]


def _yaml_parse_check(block: CodeBlock) -> list[SanityFinding]:
    """Parse a full-file YAML block with ``yaml.safe_load`` (advisory)."""
    try:
        yaml.safe_load(block.content)
    except yaml.YAMLError as exc:
        return [
            SanityFinding(
                check="sanity_yaml_parse",
                severity="advisory",
                message=f"YAML block does not parse: {_first_line(str(exc))[:_MAX_EVIDENCE]}",
                evidence=block.path or "",
            )
        ]
    return []


def _json_parse_check(block: CodeBlock) -> list[SanityFinding]:
    """Parse a full-file JSON block with ``json.loads`` (advisory)."""
    try:
        json.loads(block.content)
    except json.JSONDecodeError as exc:
        return [
            SanityFinding(
                check="sanity_json_parse",
                severity="advisory",
                message=f"JSON block does not parse: {_first_line(str(exc))[:_MAX_EVIDENCE]}",
                evidence=block.path or "",
            )
        ]
    return []


def _language_checks(parsed: _ParsedResponse) -> list[SanityFinding]:
    """Best-effort parse/lint checks for full-file PHP/YAML/JSON blocks.

    Diffs (``PATCH:`` blocks or ``diff`` fences) are skipped — they are not
    full files.  When no ``php`` binary is on the host the lint is recorded
    as ``skipped`` once instead of being attempted per block.
    """
    findings: list[SanityFinding] = []
    php_missing_reported = False

    for block, info in parsed.blocks:
        if block.kind == "patch":
            continue

        lang = info.lower()
        if lang == "diff" or _looks_like_diff(block.content):
            continue

        path_lower = (block.path or "").lower()

        if lang == "php" or path_lower.endswith(".php") or "<?php" in block.content:
            if shutil.which("php") is None:
                if not php_missing_reported:
                    php_missing_reported = True
                    findings.append(
                        SanityFinding(
                            check="sanity_php_skipped",
                            severity="skipped",
                            message="php binary not available; php -l skipped",
                            evidence=block.path or "",
                        )
                    )
                continue
            findings.extend(_php_lint(block))
        elif lang in ("yaml", "yml") or path_lower.endswith((".yml", ".yaml")):
            findings.extend(_yaml_parse_check(block))
        elif lang == "json" or path_lower.endswith(".json") or (not lang and _looks_like_json(block.content)):
            findings.extend(_json_parse_check(block))

    return findings


def structural_checks(response: str) -> list[SanityFinding]:
    """Run the lean structural sanity checks over ``response``.

    Hard findings (``sanity_no_code``, ``sanity_truncated``) mean the
    response cannot be scored at all and should short-circuit the judge.
    Every other finding is ``advisory`` (or ``skipped``) and only
    informational.  Class names, namespaces and framework semantics are
    never inspected.
    """
    parsed = _parse(response)
    findings: list[SanityFinding] = []

    if parsed.fence_count == 0 and not parsed.marker_paths:
        findings.append(
            SanityFinding(
                check="sanity_no_code",
                severity="hard",
                message="No fenced code block and no FILENAME/PATCH/REPLACE marker in the response",
                evidence=_snippet(response),
            )
        )

    if parsed.open_fence or response.strip().endswith(_TRUNCATION_MARKER):
        evidence = "open ``` fence at end of response" if parsed.open_fence else _snippet(response[-_MAX_EVIDENCE:])
        findings.append(
            SanityFinding(
                check="sanity_truncated",
                severity="hard",
                message="Response ends inside an open code fence or with a truncation marker",
                evidence=evidence,
            )
        )

    if parsed.fence_count > 0 and not parsed.marker_paths:
        findings.append(
            SanityFinding(
                check="sanity_no_declared_files",
                severity="advisory",
                message="Code block present but no FILENAME/PATCH/REPLACE path declared",
            )
        )

    for path, count in Counter(parsed.marker_paths).items():
        if count > 1:
            findings.append(
                SanityFinding(
                    check="sanity_duplicate_path",
                    severity="advisory",
                    message=f"Path declared {count} times: {path}",
                    evidence=path,
                )
            )

    findings.extend(_language_checks(parsed))
    return findings


def has_hard_failure(findings: Iterable[SanityFinding]) -> bool:
    """Return ``True`` when any finding carries severity ``hard``."""
    return any(finding.severity == "hard" for finding in findings)
