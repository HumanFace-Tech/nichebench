"""Cage run artifact path discovery.

**Ownership**: This module is owned by ``CageExecutionMixin`` (mixin.py). It
contains only artifact path helpers; it does not own docker command assembly,
island topology, or subprocess handling.

**Container safety constraints**:
- Artifact paths are discovered, not created (creation is delegated).
- All paths are validated to exist before use.
- No secrets are written to artifact paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

# ------------------------------------------------------------------
# Artifact paths
# ------------------------------------------------------------------


def resolve_run_artifacts_path(workspace: Any) -> Path:
    """Resolve the run artifacts path from a workspace object.

    Args:
        workspace: Workspace instance (must have ``run_artifacts_path`` attribute).

    Returns:
        Resolved Path to the run artifacts directory.
    """
    _raw_rap = getattr(workspace, "run_artifacts_path", None)
    if _raw_rap:
        return Path(_raw_rap).resolve()
    # Fallback: workspace.path / "results" / "run"  # noqa: ERA001
    return Path(workspace.path).resolve() / "results" / "run"


def artifact_paths(output_island_host: Path) -> Dict[str, Path]:
    """Build a dict of standard artifact paths under the output island.

    Args:
        output_island_host: Host path for output island.

    Returns:
        Dict mapping artifact names to their paths.
    """
    return {
        "run_log": output_island_host / "run.log",
        "partial_trajectory": output_island_host / "opencode_partial_trajectory.json",
        "session_dump": output_island_host / "opencode_session_dump.json",
        "trace_dir": output_island_host / "trace",
    }


def read_run_log(output_island_host: Path) -> Optional[str]:
    """Read the run.log artifact if it exists.

    Args:
        output_island_host: Host path for output island.

    Returns:
        Content of run.log, or None if not found.
    """
    run_log_path = output_island_host / "run.log"
    if run_log_path.exists():
        return run_log_path.read_text(encoding="utf-8", errors="replace")
    return None


def read_partial_trajectory(output_island_host: Path) -> Optional[Dict[str, Any]]:
    """Read the partial trajectory artifact if it exists.

    Args:
        output_island_host: Host path for output island.

    Returns:
        Parsed JSON of partial trajectory, or None if not found.
    """
    partial_traj_path = output_island_host / "opencode_partial_trajectory.json"
    if partial_traj_path.exists():
        return json.loads(partial_traj_path.read_text(encoding="utf-8"))
    return None


def read_session_dump(output_island_host: Path) -> Optional[Dict[str, Any]]:
    """Read the session dump artifact if it exists.

    Args:
        output_island_host: Host path for output island.

    Returns:
        Parsed JSON of session dump, or None if not found.
    """
    session_dump_path = output_island_host / "opencode_session_dump.json"
    if session_dump_path.exists():
        return json.loads(session_dump_path.read_text(encoding="utf-8"))
    return None
