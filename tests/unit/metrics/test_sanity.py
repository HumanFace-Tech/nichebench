"""Structural sanity layer for code generation (C4).

Covers ``nichebench.metrics.sanity``: marker/fence extraction with
tolerated narration, the two hard findings (no code, truncation), the
advisory findings (undeclared path, duplicate path, YAML/JSON parse), and
the best-effort PHP lint (advisory on a syntax error, ``skipped`` when no
``php`` binary is on the host).  The layer never inspects class names,
namespaces, or framework semantics.
"""

from __future__ import annotations

import subprocess
from typing import Any, List

from nichebench.metrics.sanity import (
    CodeBlock,
    extract_blocks,
    has_hard_failure,
    structural_checks,
)

_VALID_MULTI_FILE = (
    "Here is the implementation.\n"
    "\n"
    "FILENAME: src/solution.py\n"
    "\n"
    "```python\n"
    "def solve(values):\n"
    "    return sorted(values)\n"
    "```\n"
    "\n"
    "And the helper module:\n"
    "\n"
    "FILENAME: src/helper.py\n"
    "\n"
    "```python\n"
    "def helper():\n"
    "    return 42\n"
    "```\n"
)


def _checks(findings: Any) -> List[tuple]:
    return [(finding.check, finding.severity) for finding in findings]


# ─── Extraction ───────────────────────────────────────────────────────────────


def test_valid_multi_file_response_has_no_hard_findings() -> None:
    findings = structural_checks(_VALID_MULTI_FILE)

    assert has_hard_failure(findings) is False
    assert findings == []

    blocks = extract_blocks(_VALID_MULTI_FILE)
    assert [block.path for block in blocks] == ["src/solution.py", "src/helper.py"]
    assert [block.kind for block in blocks] == ["filename", "filename"]


def test_generic_fence_is_captured_without_a_path() -> None:
    blocks = extract_blocks("```python\nprint(1)\n```")

    assert blocks == [CodeBlock(path=None, kind="fence", content="print(1)")]


def test_narration_around_blocks_is_tolerated() -> None:
    """Narration before, between and after blocks must not break parsing."""
    response = (
        "Sure — here is the form class.\n\n"
        "FILENAME: src/Form/SettingsForm.php\n\n"
        "```php\n<?php\n// settings form\n```\n\n"
        "Next, the routing file:\n\n"
        "FILENAME: src/routing.yml\n\n"
        "```yaml\nexample.route:\n  path: /example\n```\n\n"
        "That covers both files."
    )

    blocks = extract_blocks(response)
    assert [block.path for block in blocks] == ["src/Form/SettingsForm.php", "src/routing.yml"]
    assert all(block.kind == "filename" for block in blocks)
    assert "That covers both files." not in "".join(block.content for block in blocks)


# ─── Hard findings ────────────────────────────────────────────────────────────


def test_no_code_response_is_a_hard_failure() -> None:
    findings = structural_checks("I cannot help with that request.")

    assert _checks(findings) == [("sanity_no_code", "hard")]
    assert has_hard_failure(findings) is True


def test_open_fence_at_eof_is_a_hard_failure() -> None:
    response = "FILENAME: src/solution.py\n\n```python\nprint('never closed"

    findings = structural_checks(response)

    assert _checks(findings) == [("sanity_truncated", "hard")]
    assert has_hard_failure(findings) is True

    # The partial block is still extracted for forensics.
    blocks = extract_blocks(response)
    assert blocks[0].path == "src/solution.py"
    assert blocks[0].kind == "filename"


def test_response_ending_in_truncation_marker_is_a_hard_failure() -> None:
    response = "FILENAME: src/solution.py\n\n```python\nprint('ok')\n```\n... [truncated]"

    findings = structural_checks(response)

    assert _checks(findings) == [("sanity_truncated", "hard")]


# ─── Advisory findings ────────────────────────────────────────────────────────


def test_code_without_declared_paths_is_advisory() -> None:
    findings = structural_checks("Here you go:\n\n```python\nprint('ok')\n```\n")

    assert _checks(findings) == [("sanity_no_declared_files", "advisory")]
    assert has_hard_failure(findings) is False


def test_duplicate_declared_path_is_advisory() -> None:
    response = (
        "FILENAME: src/solution.py\n\n```python\na = 1\n```\n\n"
        "Again for the second edit:\n\n"
        "FILENAME: src/solution.py\n\n```python\nb = 2\n```\n"
    )

    findings = structural_checks(response)

    assert _checks(findings) == [("sanity_duplicate_path", "advisory")]
    assert findings[0].evidence == "src/solution.py"


def test_marker_without_a_fence_is_not_a_hard_failure() -> None:
    """``sanity_no_code`` needs *both* halves: no fence **and** no marker."""
    findings = structural_checks("FILENAME: src/solution.py")

    assert has_hard_failure(findings) is False


# ─── Language checks (best effort) ────────────────────────────────────────────


def test_invalid_php_is_advisory_when_php_is_available(monkeypatch: Any) -> None:
    monkeypatch.setattr("nichebench.metrics.sanity.shutil.which", lambda name: "/usr/bin/php")

    def _fake_run(*args: Any, **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="PHP Parse error: syntax error")

    monkeypatch.setattr("nichebench.metrics.sanity.subprocess.run", _fake_run)

    response = "FILENAME: src/Broken.php\n\n```php\n<?php echo 'unterminated\n```"
    findings = structural_checks(response)

    assert _checks(findings) == [("sanity_php_syntax", "advisory")]
    assert has_hard_failure(findings) is False


def test_missing_php_binary_is_skipped_not_failed(monkeypatch: Any) -> None:
    monkeypatch.setattr("nichebench.metrics.sanity.shutil.which", lambda name: None)

    response = "FILENAME: src/Broken.php\n\n```php\n<?php echo 'x\n```"
    findings = structural_checks(response)

    assert _checks(findings) == [("sanity_php_skipped", "skipped")]
    assert has_hard_failure(findings) is False


def test_valid_php_passes_the_lint_without_findings(monkeypatch: Any) -> None:
    monkeypatch.setattr("nichebench.metrics.sanity.shutil.which", lambda name: "/usr/bin/php")

    def _fake_run(*args: Any, **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(args[0], 0, stdout="No syntax errors detected", stderr="")

    monkeypatch.setattr("nichebench.metrics.sanity.subprocess.run", _fake_run)

    findings = structural_checks("FILENAME: src/Ok.php\n\n```php\n<?php echo 1;\n```")

    assert findings == []


def test_invalid_yaml_full_file_block_is_advisory() -> None:
    findings = structural_checks("FILENAME: config/install/foo.yaml\n\n```yaml\nfoo: [unclosed\n```")

    assert _checks(findings) == [("sanity_yaml_parse", "advisory")]


def test_invalid_json_full_file_block_is_advisory() -> None:
    findings = structural_checks('FILENAME: config/schema/foo.json\n\n```json\n{"a": 1,}\n```')

    assert _checks(findings) == [("sanity_json_parse", "advisory")]


def test_patches_and_diffs_are_never_parse_checked() -> None:
    """A diff is not a full file — no PHP/YAML/JSON lint runs on it."""
    response = (
        "PATCH: config/install/foo.yaml\n\n"
        "```diff\n--- a/config/install/foo.yaml\n"
        "+++ b/config/install/foo.yaml\n"
        "@@ -1 +1 @@\n-old\n+new\n```\n"
    )

    assert structural_checks(response) == []
