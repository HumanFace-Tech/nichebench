"""Cage wrapper script helpers.

Shell script bodies are stored as real .sh files in the scripts/ directory,
loaded at runtime via Path resolution. This gives editors syntax highlighting,
shellcheck linting, and meaningful diffs.
"""

from __future__ import annotations

from pathlib import Path


def _scripts_dir() -> Path:
    """Return the Path to the scripts/ directory (next to this module)."""
    return Path(__file__).resolve().parent / "scripts"


def _load_script(name: str) -> str:
    """Load a shell script body from the scripts/ directory."""
    return (_scripts_dir() / name).read_text(encoding="utf-8")


def write_cage_git_wrapper(bin_host: Path) -> Path:
    """Write cage-local wrappers that block unsafe MUT git commands.

    Args:
        bin_host: Host path to the cage bin directory

    Returns:
        Path to the git wrapper script
    """
    git_path = bin_host / "git"
    git_path.write_text(_load_script("git-wrapper.sh"), encoding="utf-8")
    git_path.chmod(0o755)

    sh_path = bin_host / "sh"
    sh_path.write_text(_load_script("sh-wrapper.sh"), encoding="utf-8")
    sh_path.chmod(0o755)

    bash_path = bin_host / "bash"
    bash_path.write_text(_load_script("bash-wrapper.sh"), encoding="utf-8")
    bash_path.chmod(0o755)

    return git_path
