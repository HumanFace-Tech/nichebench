"""Runtime scoring: filesystem check operations.

Owner: scoring package — ops layer.
Boundary: all check operations that interact with the workspace filesystem
(glob, grep, file existence).  These are workspace-relative operations.

Supported check types
---------------------
file_exists       — single file presence check.
file_glob_exists  — glob pattern match within a directory.
grep_file         — regex search within a single file.
grep_file_multi   — multiple regex patterns within a single file.
grep_dir          — regex search across files in a directory tree.
grep_dir_count    — count of files in a directory tree matching a regex.
routing_yml_contains — Drupal routing.yml pattern pair check.

Module-level constants
----------------------
_FLOATING_TAGS — frozenset of unpinned image tag names (documented in
                 ``validation.py``; re-exported here for convenience).
"""

import re
from pathlib import Path
from typing import Any, Dict, Tuple

# Re-export for use by other ops modules without an extra import.
_FLOATING_TAGS = frozenset(
    {
        "latest",
        "edge",
        "stable",
        "dev",
        "main",
        "master",
        "nightly",
        "canary",
        "test",
        "debug",
        "release",
    }
)


def rglob_multi(directory: Path, include: str):
    """Glob directory with brace-expansion support (e.g., ``*.{php,inc}``)."""
    brace_match = re.match(r"^(.*)\{([^}]+)\}(.*)$", include)
    if brace_match:
        prefix, alternatives, suffix = brace_match.groups()
        patterns = [f"{prefix}{alt.strip()}{suffix}" for alt in alternatives.split(",")]
    else:
        patterns = [include]

    seen = set()
    for pat in patterns:
        for path in directory.rglob(pat):
            if path not in seen:
                seen.add(path)
                yield path


def op_file_exists(workspace_path: Path, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """Check that a single file exists relative to the workspace."""
    path = workspace_path / str(spec["path"])
    if path.exists():
        return True, f"File exists: {spec['path']}"
    return False, f"File not found: {spec['path']}"


def op_file_glob_exists(workspace_path: Path, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """Check that at least one file matches a glob pattern within a directory."""
    directory = workspace_path / str(spec["dir"])
    pattern = str(spec["pattern"])
    if not directory.exists():
        return False, f"Directory not found: {spec['dir']}"

    matches = list(directory.glob(pattern))
    if matches:
        return True, f"Found {len(matches)} files matching {pattern} in {spec['dir']}"
    return False, f"No files matching {pattern} in {spec['dir']}"


def op_grep_file(workspace_path: Path, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """Check that a regex pattern is present in a single file."""
    path = workspace_path / str(spec["path"])
    pattern = str(spec["pattern"])
    if not path.exists():
        return False, f"File not found: {spec['path']}"

    content = path.read_text(encoding="utf-8")
    if re.search(pattern, content):
        return True, f"Pattern '{pattern}' found in {spec['path']}"
    return False, f"Pattern '{pattern}' not found in {spec['path']}"


def op_grep_file_multi(workspace_path: Path, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """Check that all regex patterns are present in a single file."""
    path = workspace_path / str(spec["path"])
    patterns = spec.get("patterns", [])
    if not path.exists():
        return False, f"File not found: {spec['path']}"

    content = path.read_text(encoding="utf-8")
    missing = [pattern for pattern in patterns if not re.search(str(pattern), content)]
    if not missing:
        return True, f"All {len(patterns)} patterns found in {spec['path']}"
    return False, f"Missing patterns in {spec['path']}: {', '.join(map(str, missing))}"


def op_grep_dir(workspace_path: Path, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """Search for a regex pattern across files in a directory tree."""
    directory = workspace_path / str(spec["dir"])
    pattern = str(spec["pattern"])
    include = str(spec.get("include", "**/*"))
    flags = spec.get("flags", [])

    if not directory.exists():
        return False, f"Directory not found: {spec['dir']}"

    re_flags = re.IGNORECASE if "case_insensitive" in flags else 0
    regex = re.compile(pattern, re_flags)

    for path in rglob_multi(directory, include):
        if path.is_file():
            try:
                if regex.search(path.read_text(encoding="utf-8")):
                    return True, f"Pattern found in {path.relative_to(workspace_path)}"
            except Exception:
                continue

    return False, f"Pattern '{pattern}' not found in {spec['dir']} matching {include}"


def op_grep_dir_count(workspace_path: Path, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """Count files in a directory tree that match a regex pattern."""
    directory = workspace_path / str(spec["dir"])
    pattern = str(spec["pattern"])
    include = str(spec.get("include", "**/*"))
    min_count = int(spec.get("min", 1))

    if not directory.exists():
        return False, f"Directory not found: {spec['dir']}"

    regex = re.compile(pattern)
    count = 0
    for path in rglob_multi(directory, include):
        if path.is_file():
            try:
                if regex.search(path.read_text(encoding="utf-8")):
                    count += 1
            except Exception:
                continue

    if count >= min_count:
        return True, f"Found {count} files (min {min_count}) matching pattern in {spec['dir']}"
    return False, f"Found only {count} files (min {min_count}) matching pattern in {spec['dir']}"


def op_routing_yml_contains(workspace_path: Path, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """Check Drupal routing.yml files for a path pattern and key pair."""
    directory = workspace_path / "web/modules/custom"
    path_pattern = str(spec["path_pattern"])
    has_key = str(spec["has_key"])

    if not directory.exists():
        return False, "web/modules/custom not found"

    path_regex = re.compile(rf"path:.*({path_pattern})")
    key_regex = re.compile(has_key)

    for path in directory.glob("**/*.routing.yml"):
        content = path.read_text(encoding="utf-8")
        if (path_regex.search(content) or re.search(path_pattern, content)) and key_regex.search(content):
            return True, f"Route matching patterns found in {path.relative_to(workspace_path)}"

    return False, f"No routing.yml found with path pattern '{path_pattern}' and key '{has_key}'"
