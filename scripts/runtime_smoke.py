#!/usr/bin/env python3
"""Cheap runtime environment smoke checks for Drupal runtime tasks.

Usage:
  python scripts/runtime_smoke.py --workspace <path>
  python scripts/runtime_smoke.py --workspace <path> --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import NamedTuple


# Each check is a CheckSpec. ``success_marker`` is optional — when set, the
# check passes ONLY if the command exits 0 AND stdout contains the marker
# substring. This guards against false positives where ``drush config:status``
# exits 0 but reports drift on stdout (Drush prints "No differences" when
# the active config matches the sync directory).
#
# Keep checks read-only: this script is a preflight/diagnostic probe,
# not a repair tool. Do not use shell pipes in subprocess command arrays —
# pass argv-style lists and let subprocess handle them safely.
class CheckSpec(NamedTuple):
    name: str
    command: list[str]
    timeout: int
    success_marker: str | None = None
    failure_hint: str = ""


CHECKS: list[CheckSpec] = [
    # --- environment readiness ---
    CheckSpec("ddev_status", ["ddev", "status"], 60),
    # --- composer ---
    CheckSpec("composer_validate", ["ddev", "composer", "validate", "--no-interaction"], 120),
    # --- drush bootstrap ---
    CheckSpec(
        "drush_status",
        ["ddev", "drush", "status", "--fields=bootstrap,drupal-version"],
        60,
    ),
    CheckSpec("drush_sql_query", ["ddev", "drush", "sql:query", "SELECT 1"], 30),
    CheckSpec(
        "drush_config_status",
        ["ddev", "drush", "config:status"],
        60,
        success_marker="No differences",
        failure_hint=(
            "Drush reported config drift on stdout. Run `ddev drush config:status` "
            "for details, then `ddev drush config:import` or `ddev drush config:export` "
            "to reconcile."
        ),
    ),
]


def _make_result(
    name: str,
    cmd: list[str],
    passed: bool,
    returncode: int | None,
    stdout: str,
    stderr: str,
    failure_reason: str = "",
) -> dict:  # type: ignore[type-arg]
    return {
        "name": name,
        "command": " ".join(cmd),
        "passed": passed,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "failure_reason": failure_reason,
    }


def run_check(workspace: Path, spec: CheckSpec) -> dict:  # type: ignore[type-arg]
    cmd = list(spec.command)
    timeout = spec.timeout
    marker = spec.success_marker
    hint = spec.failure_hint

    try:
        res = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _make_result(
            name=spec.name,
            cmd=cmd,
            passed=False,
            returncode=None,
            stdout="",
            stderr=f"timeout after {timeout}s",
            failure_reason=f"command timed out after {timeout}s",
        )
    except OSError as exc:
        return _make_result(
            name=spec.name,
            cmd=cmd,
            passed=False,
            returncode=None,
            stdout="",
            stderr=str(exc),
            failure_reason=f"failed to launch: {exc}",
        )

    if res.returncode != 0:
        return _make_result(
            name=spec.name,
            cmd=cmd,
            passed=False,
            returncode=res.returncode,
            stdout=res.stdout,
            stderr=res.stderr,
            failure_reason=f"command exited with non-zero status {res.returncode}",
        )

    if marker is not None and marker not in res.stdout and marker not in res.stderr:
        # Exit code is 0 but the sentinel substring is missing from both stdout
        # AND stderr. This catches the Drush `config:status` drift case:
        # Drush sometimes routes the "No differences" notice to stderr instead
        # of stdout depending on verbosity settings, so we check both streams.
        preview = (res.stdout or res.stderr or "").strip().splitlines()
        detail = preview[-1] if preview else "(no output)"
        reason = f"stdout/stderr missing success marker {marker!r}; drift suspected"
        if hint:
            reason = f"{reason}. {hint}"
        # Append the offending line for debuggability.
        reason = f"{reason} (last line: {detail})"
        return _make_result(
            name=spec.name,
            cmd=cmd,
            passed=False,
            returncode=res.returncode,
            stdout=res.stdout,
            stderr=res.stderr,
            failure_reason=reason,
        )

    return _make_result(
        name=spec.name,
        cmd=cmd,
        passed=True,
        returncode=res.returncode,
        stdout=res.stdout,
        stderr=res.stderr,
    )


def _print_setup_error(code: str, message: str, json_output: bool) -> None:
    error = {"error": {"code": code, "message": message}}
    if json_output:
        print(json.dumps(error, indent=2))
    else:
        print(f"Error: {message}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Drupal runtime smoke checks")
    ap.add_argument("--workspace", required=True, help="Workspace root path")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    args = ap.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.exists():
        _print_setup_error(
            "workspace_not_found",
            f"workspace not found: {workspace}",
            args.json,
        )
        return 2

    ddev_config = workspace / ".ddev" / "config.yaml"
    if not workspace.is_dir() or not ddev_config.is_file():
        _print_setup_error(
            "not_a_ddev_project",
            f"workspace is not a DDEV project: expected {ddev_config}",
            args.json,
        )
        return 2

    results = [run_check(workspace, spec) for spec in CHECKS]
    passed = sum(1 for r in results if r["passed"])
    summary = {
        "workspace": str(workspace),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "checks": results,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Workspace: {summary['workspace']}")
        print(f"Checks: {summary['passed']}/{summary['total']} passed")
        for r in results:
            mark = "[OK]" if r["passed"] else "[FAIL]"
            print(f"{mark} {r['name']}: {r['command']}")
            if not r["passed"]:
                if r["failure_reason"]:
                    print(f"    -> {r['failure_reason']}")
                else:
                    err = (r["stderr"] or r["stdout"] or "").strip().splitlines()
                    preview = err[-1] if err else "(no output)"
                    print(f"    -> {preview}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
