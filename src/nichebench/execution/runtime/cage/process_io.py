"""Subprocess I/O helpers for cage container execution.

**Ownership**: This module is owned by ``CageExecutionMixin`` (mixin.py). It
contains subprocess I/O helpers that are currently unused by the main mixin
but available for future extraction.

**Container safety constraints**:
- All subprocess calls are made with safe defaults.
- Stream readers are daemon threads to prevent blocking container cleanup.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def force_remove_cage_container(container_name: str) -> None:
    """Force-remove a cage container (best-effort).

    Args:
        container_name: Name of the container to remove.
    """
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        check=False,
        capture_output=True,
        text=True,
    )


def write_run_log(
    output_island_host: Path,
    stdout_text: str,
    stderr_text: str,
) -> str:
    """Write combined run log to output island.

    Args:
        output_island_host: Host path for output island.
        stdout_text: Captured stdout text.
        stderr_text: Captured stderr text.

    Returns:
        The combined run log string.
    """
    run_log = f"STDOUT:\n{stdout_text}\n\nSTDERR:\n{stderr_text}".strip()
    (output_island_host / "run.log").write_text(run_log, encoding="utf-8")
    return run_log
