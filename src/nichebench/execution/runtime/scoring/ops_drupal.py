"""Runtime scoring: Drupal / Drush check operations.

Owner: scoring package — ops layer.
Boundary: all check operations that require ``ddev drush`` or a drush binary.
These operations run inside the DDEV container environment.

Supported check types
---------------------
drush_output_contains    — run a drush command and check its combined output.
drush_status_field       — read a drush core:status field and match against a regex.
drush_watchdog_clean     — check watchdog for recent PHP errors.
drush_config_status_clean — verify Drupal config is in sync.
drush_pm_enabled         — verify a Drupal module is enabled.
"""

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _make_run_command(workspace_path: Path, command_timeout_seconds: int) -> Callable[[Any], Dict[str, Any]]:
    """Return a command-runner closure bound to a specific workspace and timeout."""

    def _run_command(cmd: Any) -> Dict[str, Any]:
        """Run a command and return stdout/stderr plus pass/fail metadata."""
        try:
            run_cmd = ["ddev", "exec", "--", cmd] if isinstance(cmd, str) else cmd
            result = subprocess.run(
                run_cmd,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=max(command_timeout_seconds, 1),
            )
            return {
                "passed": True,
                "message": result.stdout or "Command passed",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.CalledProcessError as e:
            return {
                "passed": False,
                "message": f"Command failed: {e.stderr or e.stdout}",
                "stdout": e.stdout,
                "stderr": e.stderr,
                "returncode": e.returncode,
            }
        except FileNotFoundError:
            return {
                "passed": False,
                "message": "ddev command not found",
                "stdout": "",
                "stderr": "ddev command not found",
                "returncode": 127,
            }

    return _run_command


def op_drush_output_contains(
    workspace_path: Path,
    drush_cmd: Optional[List[str]],
    command_timeout_seconds: int,
    spec: Dict[str, Any],
) -> Tuple[bool, str]:
    """Run a drush command and check its combined output for a regex pattern."""
    if not drush_cmd:
        return False, "Drush/DDEV not available"

    run_cmd = _make_run_command(workspace_path, command_timeout_seconds)
    result = run_cmd(drush_cmd + shlex.split(str(spec["command"])))
    combined = str(result.get("stdout", "")) + str(result.get("stderr", ""))
    pattern = str(spec["contains"])
    if re.search(pattern, combined):
        return True, f"Output matches '{pattern}'"
    return False, f"Output does not match '{pattern}'. Output: {combined[:200]}..."


def op_drush_status_field(
    workspace_path: Path,
    drush_cmd: Optional[List[str]],
    command_timeout_seconds: int,
    spec: Dict[str, Any],
) -> Tuple[bool, str]:
    """Read a drush core:status field and match its value against a regex."""
    if not drush_cmd:
        return False, "Drush/DDEV not available"

    field = str(spec["field"])
    matches = str(spec["matches"])
    run_cmd = _make_run_command(workspace_path, command_timeout_seconds)
    result = run_cmd(drush_cmd + ["core:status", f"--field={field}"])
    value = str(result.get("stdout", "")).strip()
    if re.search(matches, value):
        return True, f"Field '{field}' value '{value}' matches '{matches}'"
    return False, f"Field '{field}' value '{value}' does not match '{matches}'"


def op_drush_watchdog_clean(
    workspace_path: Path,
    drush_cmd: Optional[List[str]],
    command_timeout_seconds: int,
    spec: Dict[str, Any],
) -> Tuple[bool, str]:
    """Check watchdog for recent PHP errors (severity=3, type=php)."""
    del spec  # No parameters needed beyond the fixed watchdog query.
    if not drush_cmd:
        return False, "Drush/DDEV not available"

    run_cmd = _make_run_command(workspace_path, command_timeout_seconds)
    result = run_cmd(drush_cmd + ["watchdog:show", "--count=50", "--severity=3", "--type=php"])
    combined = str(result.get("stdout", "")) + str(result.get("stderr", ""))
    if re.search(r"error|fatal|exception", combined, re.IGNORECASE):
        return False, f"PHP errors found in watchdog: {combined[:200]}..."
    return True, "No PHP errors in watchdog"


def op_drush_config_status_clean(
    workspace_path: Path,
    drush_cmd: Optional[List[str]],
    command_timeout_seconds: int,
    spec: Dict[str, Any],
) -> Tuple[bool, str]:
    """Verify Drupal config is in sync (no differences)."""
    del spec  # No parameters needed beyond the fixed config:status command.
    if not drush_cmd:
        return False, "Drush/DDEV not available"

    run_cmd = _make_run_command(workspace_path, command_timeout_seconds)
    result = run_cmd(drush_cmd + ["config:status"])
    combined = str(result.get("stdout", "")) + str(result.get("stderr", ""))
    if not combined.strip() or "No differences" in combined:
        return True, "Config is in sync"
    return False, "Config is out of sync"


def op_drush_pm_enabled(
    workspace_path: Path,
    drush_cmd: Optional[List[str]],
    command_timeout_seconds: int,
    spec: Dict[str, Any],
) -> Tuple[bool, str]:
    """Verify a Drupal module is enabled."""
    if not drush_cmd:
        return False, "Drush/DDEV not available"

    module = str(spec["module"])
    run_cmd = _make_run_command(workspace_path, command_timeout_seconds)
    result = run_cmd(drush_cmd + ["pm:list", "--status=enabled", f"--filter={module}", "--format=json"])
    if module in str(result.get("stdout", "")):
        return True, f"Module '{module}' is enabled"
    return False, f"Module '{module}' is not enabled"
