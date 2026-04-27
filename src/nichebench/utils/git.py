import subprocess
from pathlib import Path


class GitError(Exception):
    """Exception raised for git operation errors."""

    pass


def resolve_branch_to_sha(branch_name: str, repo_path: Path) -> str:
    """Resolve a branch name to its current commit SHA."""
    try:
        # Try origin first
        result = subprocess.run(
            ["git", "rev-parse", f"origin/{branch_name}"], cwd=repo_path, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        try:
            # Fallback to local branch
            result = subprocess.run(
                ["git", "rev-parse", branch_name], cwd=repo_path, capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise GitError(f"Failed to resolve branch {branch_name} to SHA: {e.stderr}")


def find_git_root(start_path: Path) -> Path:
    """Find git repository root from a starting path."""
    current = start_path.resolve()
    if current.is_file():
        current = current.parent

    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            raise GitError(f"No git repository found from start path: {start_path}")
        current = current.parent


def get_current_sha(repo_path: Path) -> str:
    """Get the current HEAD commit SHA."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to get current SHA: {e.stderr}")


def checkout_sha(sha: str, repo_path: Path, force: bool = False):
    """Checkout a specific SHA in the given repo."""
    try:
        cmd = ["git", "checkout", sha]
        if force:
            cmd.append("-f")

        subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to checkout SHA {sha}: {e.stderr}")


def create_and_checkout_branch(branch_name: str, base_sha: str, repo_path: Path):
    """Create a new branch from a base SHA and check it out."""
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch_name, base_sha], cwd=repo_path, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to create branch {branch_name}: {e.stderr}")
