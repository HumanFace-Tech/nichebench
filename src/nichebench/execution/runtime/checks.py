"""Runtime check resolution: loading and normalising checks from task manifests.

Check resolution maps human-readable check references in a manifest (e.g.
``"phpstan_clean"``) to fully-specified check dictionaries that
``RuntimeScorer.run_deterministic_checks`` can execute.

Resolution order
--------------
1. If ``checks`` is a plain list, pass through ``normalize_checks`` immediately.
2. If ``checks`` is a dict, first try to resolve each entry against a
   ``checks/<task_id>.yaml`` sidecar file (loaded by ``load_runtime_checks``).
3. Unresolved string entries that look like shell commands become inline
   ``fail_to_pass`` / ``pass_to_pass`` checks.
4. Unresolved string entries that do not look like commands become
   ``unknown_runtime_check_id`` results (still returned so the scorer can
   surface them).

Relationship to scorer
--------------------
- ``resolve_runtime_checks_file`` and ``load_runtime_checks`` produce the
  list of check dicts that ``RuntimeScorer.run_deterministic_checks`` executes.
- ``looks_like_shell_command`` is a heuristic used only during resolution;
  it is not used by the scorer itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from nichebench.core.datamodel import TestCaseSpec


def looks_like_shell_command(value: str) -> bool:
    """Heuristic: returns True if value contains whitespace (looks like a shell command)."""
    return any(ch.isspace() for ch in value)


def resolve_runtime_checks_file(test_case: TestCaseSpec) -> Optional[Path]:
    """Locate a task's sidecar checks file, if one exists.

    The sidecar must be at ``<tasks_dir>/checks/<manifest_basename>`` relative
    to the manifest directory structure.  Returns None if the file does not exist.
    """
    if not test_case.file_path:
        return None
    manifest_path = Path(test_case.file_path)
    if manifest_path.parent.name != "manifest":
        return None
    if manifest_path.parent.parent.name != "tasks":
        return None
    checks_path = manifest_path.parent.parent / "checks" / manifest_path.name
    return checks_path if checks_path.exists() else None


def load_runtime_checks_by_id(
    checks_path: Path,
    parsed: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """Load a checks YAML file and index it by check id.

    Pass ``parsed`` to reuse YAML already read from ``checks_path`` so the
    file is only read once. Returns an empty dict if the file is missing,
    unreadable, or has no parseable ``checks`` list.
    """
    if parsed is None:
        try:
            parsed = yaml.safe_load(checks_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    if not isinstance(parsed, dict):
        return {}
    checks = parsed.get("checks")
    if not isinstance(checks, list):
        return {}

    by_id: Dict[str, Dict[str, Any]] = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_id = check.get("id")
        if check_id is not None:
            by_id[str(check_id)] = check
    return by_id


def _criticality_overrides(sidecar: Any) -> Dict[str, Any]:
    """Extract the ``required_command_criticality`` map from a parsed sidecar.

    Returns an empty dict when the sidecar is missing, not a mapping, or the
    ``required_command_criticality`` key is absent or not a mapping.
    """
    if not isinstance(sidecar, dict):
        return {}
    overrides = sidecar.get("required_command_criticality", {})
    return overrides if isinstance(overrides, dict) else {}


def _resolve_criticality(overrides: Dict[str, Any], command: str) -> bool:
    """Resolve the criticality of a required command.

    Honors an explicit boolean override; a missing key or a malformed
    (non-boolean) override falls back to ``True`` (critical by default).
    """
    critical = overrides.get(command, True)
    return critical if isinstance(critical, bool) else True


def _apply_required_command_criticality(
    normalized: List[Dict[str, Any]],
    overrides: Dict[str, Any],
) -> None:
    """Apply ``required_command_criticality`` overrides in place.

    Only ``required_command`` checks are adjusted. Missing keys or malformed
    (non-boolean) overrides leave the check at its (critical) default of True.
    """
    for check in normalized:
        if check.get("type") != "required_command":
            continue
        command = check.get("command")
        if command is None:
            continue
        check["critical"] = _resolve_criticality(overrides, str(command))


def load_runtime_checks(
    test_case: TestCaseSpec,
    normalize_checks: Callable[[Any], List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Load and normalise runtime checks for a test case.

    Resolution steps (in order):
      1. If ``raw_checks`` is a list, normalise and return immediately.
      2. If a sidecar ``checks/<id>.yaml`` file exists, resolve string
         references against it before normalising.
      3. Fall back to inline normalisation.

    When the sidecar resolves no checks we still honour any
    ``required_command_criticality`` overrides declared on it, so criticality
    is never silently pinned to ``True`` for an empty checks list.
    """
    raw_checks = test_case.raw.get("checks", [])
    if not isinstance(raw_checks, dict):
        return normalize_checks(raw_checks)

    checks_path = resolve_runtime_checks_file(test_case)
    if checks_path is None:
        return normalize_checks(raw_checks)

    # Read the sidecar once: it indexes check ids and, when present, declares
    # ``required_command_criticality`` overrides for required_commands.
    try:
        sidecar = yaml.safe_load(checks_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        sidecar = None

    overrides = _criticality_overrides(sidecar)
    checks_by_id = load_runtime_checks_by_id(checks_path, parsed=sidecar) if isinstance(sidecar, dict) else {}

    if not checks_by_id:
        # The sidecar resolves no checks. Keep fallback normalisation for the
        # categories and allowed_paths, but still honour any
        # required_command_criticality overrides for required_commands.
        fallback = normalize_checks(raw_checks)
        _apply_required_command_criticality(fallback, overrides)
        return fallback

    normalized: List[Dict[str, Any]] = []
    critical_categories = {"fail_to_pass", "pass_to_pass", "static"}

    for category in ("fail_to_pass", "pass_to_pass", "static"):
        entries = raw_checks.get(category, [])
        if not isinstance(entries, list):
            continue
        for item in entries:
            item_text = str(item)
            resolved = checks_by_id.get(item_text)
            if isinstance(resolved, dict):
                concrete = dict(resolved)
                concrete.setdefault("id", item_text)
                concrete.setdefault("category", category)
                concrete.setdefault("critical", category in critical_categories)
                normalized.append(concrete)
                continue

            if looks_like_shell_command(item_text):
                normalized.append(
                    {
                        "name": item_text,
                        "type": category,
                        "command": item_text,
                        "critical": category in critical_categories,
                    }
                )
                continue

            normalized.append(
                {
                    "name": item_text,
                    "type": "unknown_runtime_check_id",
                    "id": item_text,
                    "category": category,
                    "critical": category in critical_categories,
                    "message": f"Unknown runtime check id: {item_text}",
                }
            )

    for cmd in raw_checks.get("required_commands", []):
        normalized.append(
            {
                "name": str(cmd),
                "type": "required_command",
                "command": str(cmd),
                "critical": _resolve_criticality(overrides, str(cmd)),
            }
        )
    if raw_checks.get("allowed_paths"):
        normalized.append(
            {
                "name": "path_policy",
                "type": "path_policy",
                "allowed_paths": raw_checks.get("allowed_paths", []),
                "critical": True,
            }
        )

    return normalized
