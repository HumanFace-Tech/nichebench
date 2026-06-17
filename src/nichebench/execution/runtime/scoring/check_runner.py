"""Runtime scoring: check dispatch runner.

Owner: scoring package.
Boundary: translates a check dict (with ``op`` field) into a ``CheckResult``
by delegating to the appropriate ops module function.  This is the routing
layer — it holds no business logic beyond dispatch.

Public API
----------
run_check(op, spec, *, workspace_path, drush_cmd=None, command_timeout_seconds=1800)
    Dispatch a single check dict and return a ``CheckResult``.
    ``drush_cmd`` is required for drush-based ops; pass ``None`` if DDEV
    is not available and the op will return a "not available" result.

Module-level constants
----------------------
FLOATING_TAGS — frozenset of tag names considered unpinned (documented in
                ``validation.py``; re-exported here for cross-module convenience).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from nichebench.execution.runtime.scoring.datamodel import CheckResult

from . import ops_drupal, ops_filesystem, ops_static_analysis

# Re-export for backward compatibility with code that imported from the
# old module-level constants.
FLOATING_TAGS = ops_filesystem._FLOATING_TAGS


def run_check(
    op: str,
    spec: Dict[str, Any],
    *,
    workspace_path: Path,
    drush_cmd: Optional[List[str]] = None,
    command_timeout_seconds: int = 1800,
) -> CheckResult:
    """Dispatch an op-based check to its handler function and return a CheckResult."""
    name = str(spec.get("label") or spec.get("name") or spec.get("id") or op or "Unnamed check")
    is_critical = bool(spec.get("critical", True))

    # Map op -> (handler_fn, args_tuple).  Each handler has a distinct signature;
    # we pass only the arguments it declares.
    handlers: Dict[str, Any] = {
        # Filesystem ops (workspace-relative, no drush needed)
        "file_exists": lambda: ops_filesystem.op_file_exists(workspace_path, spec),
        "file_glob_exists": lambda: ops_filesystem.op_file_glob_exists(workspace_path, spec),
        "grep_file": lambda: ops_filesystem.op_grep_file(workspace_path, spec),
        "grep_file_multi": lambda: ops_filesystem.op_grep_file_multi(workspace_path, spec),
        "grep_dir": lambda: ops_filesystem.op_grep_dir(workspace_path, spec),
        "grep_dir_count": lambda: ops_filesystem.op_grep_dir_count(workspace_path, spec),
        "routing_yml_contains": lambda: ops_filesystem.op_routing_yml_contains(workspace_path, spec),
        # Drupal/drush ops (require drush_cmd)
        "drush_output_contains": lambda: ops_drupal.op_drush_output_contains(
            workspace_path, drush_cmd, command_timeout_seconds, spec
        ),
        "drush_status_field": lambda: ops_drupal.op_drush_status_field(
            workspace_path, drush_cmd, command_timeout_seconds, spec
        ),
        "drush_watchdog_clean": lambda: ops_drupal.op_drush_watchdog_clean(
            workspace_path, drush_cmd, command_timeout_seconds, spec
        ),
        "drush_config_status_clean": lambda: ops_drupal.op_drush_config_status_clean(
            workspace_path, drush_cmd, command_timeout_seconds, spec
        ),
        "drush_pm_enabled": lambda: ops_drupal.op_drush_pm_enabled(
            workspace_path, drush_cmd, command_timeout_seconds, spec
        ),
        # Static analysis ops
        "composer_script_clean": lambda: ops_static_analysis.op_composer_script_clean(
            workspace_path, command_timeout_seconds, spec
        ),
        "phpstan_clean": lambda: ops_static_analysis.op_phpstan_clean(workspace_path, command_timeout_seconds, spec),
    }

    handler = handlers.get(op)
    if not handler:
        return CheckResult(
            name=name,
            type=op or "unknown",
            passed=False,
            message=f"Unknown operation: {op}",
            is_critical=is_critical,
        )

    outcome = handler()
    if isinstance(outcome, tuple) and len(outcome) == 3:
        raw_passed, raw_message, raw_details = outcome
        passed = bool(raw_passed)
        message = str(raw_message)
        details = raw_details if isinstance(raw_details, dict) else {}
    else:
        raw_passed, raw_message = outcome  # type: ignore[misc]
        passed = bool(raw_passed)
        message = str(raw_message)
        details = {}

    return CheckResult(
        name=name,
        type=op,
        passed=passed,
        message=message,
        is_critical=is_critical,
        details=details,
    )
