import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    print(f"Running git {' '.join(args)} in {cwd}")
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
    return result


def open_conflict_pr(repo_path: Path, seed_branch: str, conflicts: list[str]) -> None:
    """Create optional auto-PR with conflict report for manual resolution."""
    if not conflicts:
        return

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    report_branch = f"sync-conflict-report-{timestamp}"
    report_file = repo_path / ".nichebench" / f"sync-conflicts-{timestamp}.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(
        "\n".join(
            [
                "# Seed Sync Conflict Report",
                "",
                f"Generated: {timestamp} UTC",
                f"Seed branch: {seed_branch}",
                "",
                "## Branches requiring manual conflict resolution",
                *[f"- `{branch}`" for branch in conflicts],
            ]
        ),
        encoding="utf-8",
    )

    run_git(["checkout", "-b", report_branch], repo_path)
    run_git(["add", str(report_file.relative_to(repo_path))], repo_path)
    run_git(["commit", "-m", "chore: add seed sync conflict report"], repo_path)
    push = run_git(["push", "-u", "origin", report_branch], repo_path)
    if push.returncode != 0:
        print("Failed to push report branch; skipping PR creation.")
        return

    pr_cmd = [
        "gh",
        "pr",
        "create",
        "--base",
        seed_branch,
        "--head",
        report_branch,
        "--title",
        "chore: report seed sync conflicts",
        "--body",
        "Automated report listing task branches with merge conflicts during seed sync.",
    ]
    pr_result = subprocess.run(pr_cmd, cwd=repo_path, capture_output=True, text=True)
    if pr_result.returncode == 0:
        print(f"Created PR: {pr_result.stdout.strip()}")
    else:
        print(f"Failed to create PR automatically: {pr_result.stderr}")


def sync_data_repo(
    repo_path: Path,
    seed_branch: str = "seed/main",
    task_prefix: str = "task/",
    auto_pr: bool = False,
) -> None:
    """Sync all task branches with seed branch and publish status."""
    if not repo_path.exists():
        print(f"Repo path {repo_path} does not exist.")
        return

    run_git(["fetch", "origin"], repo_path)

    result = run_git(["branch", "-r", "--list", f"origin/{task_prefix}*"], repo_path)
    branches = [b.strip().replace("origin/", "") for b in result.stdout.splitlines() if b.strip()]

    print(f"Found {len(branches)} task branches to sync.")
    sync_status: dict[str, str] = {}
    conflicts: list[str] = []

    for branch in branches:
        print(f"--- Syncing {branch} ---")
        run_git(["checkout", branch], repo_path)
        run_git(["reset", "--hard", f"origin/{branch}"], repo_path)

        merge_result = run_git(["merge", f"origin/{seed_branch}"], repo_path)
        if merge_result.returncode == 0:
            print(f"Successfully merged {seed_branch} into {branch}")
            sync_status[branch] = "success"
        else:
            print(f"Merge conflict in {branch}. Requires manual resolution.")
            sync_status[branch] = "conflict"
            conflicts.append(branch)
            run_git(["merge", "--abort"], repo_path)

    print("\n--- Sync Status Summary ---")
    for branch, status in sync_status.items():
        print(f"{branch}: {status}")

    if auto_pr:
        open_conflict_pr(repo_path, seed_branch, conflicts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync seed branch into task branches.")
    parser.add_argument("repo_path", type=Path, help="Path to data repository")
    parser.add_argument("--seed-branch", default="seed/main", help="Seed branch name")
    parser.add_argument("--task-prefix", default="task/", help="Task branch prefix")
    parser.add_argument(
        "--auto-pr",
        action="store_true",
        help="Create optional conflict report PR for branches requiring manual resolution",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.repo_path:
        print("Usage: python sync_data_branches.py <data_repo_path>")
        sys.exit(1)

    sync_data_repo(
        repo_path=args.repo_path,
        seed_branch=args.seed_branch,
        task_prefix=args.task_prefix,
        auto_pr=args.auto_pr,
    )
