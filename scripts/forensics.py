#!/usr/bin/env python3
"""NicheBench forensics tool — analyse trial and run artifacts.

Usage:
    poetry run nichebench forensics --path <run_or_trial_path>
    poetry run nichebench forensics --path <run_or_trial_path> --json

This script wrapper assumes the package is installed or the Poetry environment
is active. Prefer the CLI command above for normal use.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nichebench.execution.diagnostics import collect_reports, format_text_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyse NicheBench trial/run artifacts and produce a forensics report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--path",
        required=True,
        type=Path,
        metavar="PATH",
        help="Trial directory or run directory to analyse.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON array instead of human text.",
    )
    args = parser.parse_args()

    path: Path = args.path.resolve()
    if not path.exists():
        print(f"[forensics] ERROR: Path does not exist: {path}", file=sys.stderr)
        return 1

    reports = collect_reports(path)
    if not reports:
        print("[forensics] No trials found at the given path.", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(reports, indent=2, default=str))
    else:
        print(format_text_report(reports))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
