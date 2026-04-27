#!/usr/bin/env python3
"""Conservative runtime cleanup and Docker disk maintenance helpers."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _iter_run_dirs(root: Path):
    if not root.exists():
        return
    for path in root.glob("run-*"):
        if path.is_dir():
            yield path


def cleanup_workspaces(workspaces_dir: Path, max_age_days: int, dry_run: bool) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    removed = 0
    for run_dir in _iter_run_dirs(workspaces_dir):
        mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
        if mtime >= cutoff:
            continue
        print(f"[cleanup] {run_dir}")
        if not dry_run:
            shutil.rmtree(run_dir, ignore_errors=True)
        removed += 1
    return removed


def prune_docker(aggressive: bool, dry_run: bool) -> int:
    commands = [["docker", "builder", "prune", "-f"], ["docker", "system", "prune", "-f"]]
    if aggressive:
        commands[-1] = ["docker", "system", "prune", "-af", "--volumes"]

    if dry_run:
        for cmd in commands:
            print("[dry-run] " + " ".join(cmd))
        return 0

    for cmd in commands:
        subprocess.run(cmd, check=True)
    return len(commands)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Runtime cleanup and Docker maintenance.")
    parser.add_argument("--workspaces-dir", type=Path, default=Path("workspaces"), help="Workspace root")
    parser.add_argument("--max-age-days", type=int, default=7, help="Delete only workspaces older than this")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without deleting/pruning")
    parser.add_argument("command", choices=("cleanup-workspaces", "prune-docker"), help="Maintenance command")
    parser.add_argument("--aggressive", action="store_true", help="Also remove unused volumes/images")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "cleanup-workspaces":
        removed = cleanup_workspaces(args.workspaces_dir, args.max_age_days, args.dry_run)
        print(f"Removed {removed} workspace(s)")
        return 0

    if args.command == "prune-docker":
        removed = prune_docker(args.aggressive, args.dry_run)
        print(f"Ran {removed} docker prune command(s)")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
