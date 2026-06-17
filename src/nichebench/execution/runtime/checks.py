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


def load_runtime_checks_by_id(checks_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load a checks YAML file and index it by check id.

    Returns an empty dict if the file is missing, unreadable, or has no
    parseable ``checks`` list.
    """
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
    """
    raw_checks = test_case.raw.get("checks", [])
    if not isinstance(raw_checks, dict):
        return normalize_checks(raw_checks)

    checks_path = resolve_runtime_checks_file(test_case)
    if checks_path is None:
        return normalize_checks(raw_checks)

    checks_by_id = load_runtime_checks_by_id(checks_path)
    if not checks_by_id:
        return normalize_checks(raw_checks)

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
        normalized.append({"name": str(cmd), "type": "required_command", "command": str(cmd), "critical": True})
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
