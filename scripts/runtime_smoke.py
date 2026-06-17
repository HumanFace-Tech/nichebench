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

# Each entry: (check_name, command_list, timeout_seconds). Keep checks
# read-only: this script is a preflight/diagnostic probe, not a repair tool.
CHECKS: list[tuple[str, list[str], int]] = [
    # --- environment readiness ---
    ("ddev_status", ["ddev", "status"], 60),
    # --- composer ---
    ("composer_validate", ["ddev", "composer", "validate", "--no-interaction"], 120),
    # --- drush bootstrap ---
    ("drush_status", ["ddev", "drush", "status", "--fields=bootstrap,drupal-version"], 60),
    ("drush_sql_query", ["ddev", "drush", "sql:query", "SELECT 1"], 30),
    ("drush_config_status", ["ddev", "drush", "config:status"], 60),
]


def run_check(workspace: Path, name: str, cmd: list[str], timeout: int = 180) -> dict:  # type: ignore[type-arg]
    try:
        res = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        passed = res.returncode == 0
        return {
            "name": name,
            "command": " ".join(cmd),
            "passed": passed,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "command": " ".join(cmd),
            "passed": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
        }
    except OSError as exc:
        return {
            "name": name,
            "command": " ".join(cmd),
            "passed": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="Drupal runtime smoke checks")
    ap.add_argument("--workspace", required=True, help="Workspace root path")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    args = ap.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.exists():
        print(json.dumps({"error": f"workspace not found: {workspace}"}, indent=2))
        return 2

    results = [run_check(workspace, name, cmd, timeout) for name, cmd, timeout in CHECKS]
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
            mark = "✅" if r["passed"] else "❌"
            print(f"{mark} {r['name']}: {r['command']}")
            if not r["passed"]:
                err = (r["stderr"] or r["stdout"] or "").strip().splitlines()
                preview = err[-1] if err else "(no output)"
                print(f"    ↳ {preview}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
