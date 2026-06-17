"""Git diff capture for runtime workspace artifacts.

Ownership
--------
This module is owned by the workspace package.  It contains diff and git log
capture functions that are called from ``Workspace`` methods.

Side-effect boundaries
---------------------
- Runs ``git diff`` and ``git log`` commands in the workspace.
- Runs ``git add`` to stage working-tree changes before diffing.
- Logs all operations to the provided ``command_log`` list.
- Does NOT own workspace creation or DDEV operations.
"""

import subprocess
from pathlib import Path
from typing import Optional


def capture_diff(path: Path) -> str:
    """Capture current git diff for workspace artifacts.

    Args:
        path: Workspace path (used as CWD for git commands).

    Returns:
        Git diff output string.

    Raises:
        WorkspaceError: If the git command fails.
    """
    from nichebench.execution.runtime.workspace.model import WorkspaceError

    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        raise WorkspaceError(f"Failed to capture diff: {exc.stderr}") from exc


def capture_final_diff(path: Path, base_sha: Optional[str], command_log: list[dict[str, object]]) -> str:
    """Capture baseline-to-final diff for runtime artifacts.

    Stages all working-tree changes before diffing so that new files written
    by the agent (which are untracked in git) are included in the output.

    Args:
        path: Workspace path (used as CWD for git commands).
        base_sha: Base SHA to diff against (or None for HEAD).
        command_log: Shared command log list to append entries to.

    Returns:
        Git diff output string.

    Raises:
        WorkspaceError: If the git command fails.
    """
    from nichebench.execution.runtime.workspace.model import WorkspaceError

    try:
        # Stage task-relevant changes while excluding harness-generated files.
        add_cmd = [
            "git",
            "add",
            "-A",
            "--",
            ".",
            ":(exclude)AGENTS.md",
            ":(exclude)AGENTS.mut.md",
            ":(exclude)TASK.md",
            ":(exclude)HINTS.md",
            ":(exclude)opencode.json",
            ":(exclude).nichebench-runtime-task.txt",
            ":(exclude).ddev/config.yaml",
            ":(exclude).ddev/share-providers/**",
            ":(exclude)recipes/**",
            ":(exclude)results/run/**",
        ]
        subprocess.run(
            add_cmd,
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        cmd = ["git", "diff", "--cached", base_sha] if base_sha else ["git", "diff", "--cached", "HEAD"]
        result = subprocess.run(
            cmd,
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        raise WorkspaceError(f"Failed to capture final diff: {exc.stderr}") from exc


def capture_git_log(path: Path, base_sha: Optional[str], command_log: list[dict[str, object]]) -> str:
    """Capture commit history for runtime artifact bundles.

    Args:
        path: Workspace path (used as CWD for git commands).
        base_sha: Base SHA to diff against (defaults to HEAD~1 if None).
        command_log: Shared command log list to append entries to.

    Returns:
        Git log output string.

    Raises:
        WorkspaceError: If the git command fails.
    """
    from nichebench.execution.runtime.workspace.model import WorkspaceError

    if not base_sha:
        base_sha = "HEAD~1"

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"{base_sha}...HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        raise WorkspaceError(f"Failed to capture git log: {exc.stderr}") from exc
