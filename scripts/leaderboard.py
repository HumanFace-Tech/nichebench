#!/usr/bin/env python3
"""
Quick leaderboard: shows det / judge / hybrid scores for every model's latest run.

Usage:
    python scripts/leaderboard.py
    python scripts/leaderboard.py --framework drupal_runtime --task runtime
    python scripts/leaderboard.py --all-timestamps   # show every run, not just latest
"""

import argparse
import json
from pathlib import Path


def load_details(details_path: Path) -> list[dict]:
    results = []
    with details_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return results


def latest_run_dir(model_dir: Path) -> Path | None:
    subdirs = [d for d in model_dir.iterdir() if d.is_dir()]
    return max(subdirs, key=lambda d: d.name) if subdirs else None


def all_run_dirs(model_dir: Path) -> list[Path]:
    return sorted([d for d in model_dir.iterdir() if d.is_dir()], key=lambda d: d.name)


def fmt(v) -> str:
    if v is None:
        return "  —  "
    return f"{v:.3f}"


def fmt_checks(checks: list[dict]) -> str:
    passed = sum(1 for c in checks if c.get("passed"))
    total = len(checks)
    return f"{passed}/{total}"


def main():
    parser = argparse.ArgumentParser(description="NicheBench leaderboard")
    parser.add_argument("--framework", default="drupal_runtime")
    parser.add_argument("--task", default="runtime")
    parser.add_argument("--results-root", default="results")
    parser.add_argument("--all-timestamps", action="store_true", help="Show every run, not just latest")
    args = parser.parse_args()

    root = Path(args.results_root) / args.framework / args.task
    if not root.exists():
        print(f"No results at {root}")
        return

    rows: list[tuple] = []
    for model_dir in sorted(root.iterdir()):
        if not model_dir.is_dir():
            continue
        run_dirs = all_run_dirs(model_dir) if args.all_timestamps else [latest_run_dir(model_dir)]
        run_dirs = [r for r in run_dirs if r is not None]
        for run_dir in run_dirs:
            details_path = run_dir / "details.jsonl"
            if not details_path.exists():
                continue
            records = load_details(details_path)
            for rec in records:
                jo = rec.get("judge_output") or {}
                det = jo.get("deterministic_score")
                judge = jo.get("judge_score")
                hybrid = jo.get("hybrid_score")
                gate = jo.get("deterministic_gate_passed")
                checks = jo.get("checks", [])
                rows.append(
                    (
                        rec.get("mut_model", model_dir.name),
                        rec.get("test_id", "?"),
                        run_dir.name,
                        det,
                        judge,
                        hybrid,
                        fmt_checks(checks),
                        "✅" if gate else "❌",
                    )
                )

    # Sort by hybrid desc (None last)
    rows.sort(key=lambda r: (r[5] is None, -(r[5] or 0)))

    # Print table
    col_w = [40, 22, 18, 8, 8, 8, 8, 6]
    headers = ["Model", "Test ID", "Timestamp", "Det", "Judge", "Hybrid", "Checks", "Gate"]
    sep = "  ".join("-" * w for w in col_w)
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_w))
    print(f"\n{'NicheBench Leaderboard':^{sum(col_w) + 2 * (len(col_w) - 1)}}")
    print(f"Framework: {args.framework} / Task: {args.task}\n")
    print(header_line)
    print(sep)
    for r in rows:
        model, test_id, ts, det, judge, hybrid, checks, gate = r
        print(
            f"{model[:40]:<40}  {test_id[:22]:<22}  {ts[:18]:<18}  "
            f"{fmt(det):<8}  {fmt(judge):<8}  {fmt(hybrid):<8}  {checks:<8}  {gate}"
        )
    print()


if __name__ == "__main__":
    main()
