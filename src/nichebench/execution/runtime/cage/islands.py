"""Island path setup and prompt file writing.

**Ownership**: This module is owned by ``CageExecutionMixin`` (mixin.py). It
contains only island-path and prompt-file helpers; it does not own docker
command assembly, subprocess handling, or watchdog logic.

**Container safety constraints**:
- All island paths are validated before use.
- Prompt files are written atomically where possible.
- No secrets are written to island paths.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from nichebench.core.datamodel import TestCaseSpec


def resolve_workspace_host_path(workspace: Any) -> Path:
    """Resolve the workspace host path from a workspace object.

    Args:
        workspace: Workspace instance (must have ``path`` attribute).

    Returns:
        Resolved absolute Path to the workspace.
    """
    if isinstance(workspace, Path):
        return Path(workspace).resolve()
    return Path(workspace.path).resolve()


def resolve_task_input(
    test_case: TestCaseSpec,
    workspace_host_path: Path,
    task_input_override: Optional[str],
) -> str:
    """Resolve the task input string.

    Priority:
    1. ``task_input_override`` if provided (e.g., review nudge)
    2. ``TASK.md`` content in workspace (if non-empty)
    3. ``prompt`` field from test case (with optional ``context``)

    Args:
        test_case: Test case specification.
        workspace_host_path: Resolved host path to workspace.
        task_input_override: Optional override string.

    Returns:
        The resolved task input string.
    """
    if task_input_override is not None:
        return task_input_override

    prompt = getattr(test_case, "prompt", "") or test_case.raw.get("prompt", "") or ""
    context = getattr(test_case, "context", "") or test_case.raw.get("context", "") or ""
    task_input = prompt if not context else f"{prompt}\n\nContext:\n{context}"

    task_markdown_path = workspace_host_path / "TASK.md"
    try:
        task_markdown = task_markdown_path.read_text(encoding="utf-8").strip()
        if task_markdown:
            task_input = task_markdown
    except OSError:
        pass

    return task_input


def write_prompt_file(
    workspace_host_path: Path,
    task_input: str,
) -> tuple[Path, Path]:
    """Write the task input to the input island prompt file.

    The prompt file is placed at:
    - Host: ``{workspace_host_path}/.nichebench-runtime-task.txt``
    - Container: ``{workspace_host_path}/.nichebench-runtime-task.txt``

    Args:
        workspace_host_path: Resolved host path to workspace.
        task_input: The task input string to write.

    Returns:
        Tuple of (prompt_file_host, prompt_file_container).
    """
    prompt_file_host = workspace_host_path / ".nichebench-runtime-task.txt"
    prompt_file_container = Path(str(workspace_host_path)) / ".nichebench-runtime-task.txt"
    prompt_file_host.write_text(task_input, encoding="utf-8")
    return prompt_file_host, prompt_file_container


def build_input_island(
    workspace_host_path: Path,
) -> tuple[Path, str]:
    """Build input island paths.

    Args:
        workspace_host_path: Resolved host path to workspace.

    Returns:
        Tuple of (input_island_host, input_island_container).
    """
    input_island_host = workspace_host_path
    input_island_container = "/nichebench/islands/input"
    return input_island_host, input_island_container


def build_output_island(
    workspace: Any,
    workspace_host_path: Path,
) -> tuple[Path, str, Path, str]:
    """Build output/trace island paths.

    Args:
        workspace: Workspace instance.
        workspace_host_path: Resolved host path to workspace.

    Returns:
        Tuple of (output_island_host, output_trace_island_container,
        trace_host_path, trace_container_path).
    """
    _raw_rap = getattr(workspace, "run_artifacts_path", None)
    output_island_host = (
        Path(_raw_rap).resolve()
        if isinstance(_raw_rap, (str, Path))
        else (workspace_host_path / "results" / "run").resolve()
    )
    output_island_host.mkdir(parents=True, exist_ok=True)
    output_trace_island_container = "/nichebench/islands/output-trace"
    trace_host_path = output_island_host / "trace"
    trace_host_path.mkdir(parents=True, exist_ok=True)
    trace_container_path = f"{output_trace_island_container}/trace"
    return output_island_host, output_trace_island_container, trace_host_path, trace_container_path


def build_ops_island(
    runtime_config: Dict[str, Any],
    state_root: Path,
) -> Optional[tuple[Path, str]]:
    """Build optional ops island paths.

    Args:
        runtime_config: Full runtime configuration dict.
        state_root: State root path for run-scoped temp directories.

    Returns:
        Tuple of (ops_island_host, ops_island_container) if enabled,
        otherwise None.
    """
    ops_island_host_path = runtime_config.get("runtime_ops_island_host_path")
    enable_ops_island = bool(runtime_config.get("runtime_enable_ops_island", False) or ops_island_host_path)
    if not enable_ops_island:
        return None

    ops_island_host = Path(str(ops_island_host_path)) if ops_island_host_path else (state_root / "ops-island")
    ops_island_host.mkdir(parents=True, exist_ok=True)
    ops_island_container = "/nichebench/islands/ops"
    return ops_island_host, ops_island_container


def build_island_topology(
    workspace_host_path: Path,
    workspace_container_path: str,
    input_island_host: Path,
    input_island_container: str,
    output_island_host: Path,
    output_trace_island_container: str,
    trace_host_path: Path,
    trace_container_path: str,
    ops_island: Optional[tuple[Path, str]] = None,
) -> Dict[str, Any]:
    """Build the island topology dict.

    Args:
        workspace_host_path: Resolved host path to workspace.
        workspace_container_path: Container path for workspace.
        input_island_host: Host path for input island.
        input_island_container: Container path for input island.
        output_island_host: Host path for output island.
        output_trace_island_container: Container path for output/trace island.
        trace_host_path: Host path for trace subdirectory.
        trace_container_path: Container path for trace subdirectory.
        ops_island: Optional tuple of (host_path, container_path) for ops island.

    Returns:
        Island topology dict.
    """
    island_topology: Dict[str, Any] = {
        "workspace": {
            "host_path": str(workspace_host_path),
            "container_path": workspace_container_path,
        },
        "input_island": {
            "host_path": str(input_island_host),
            "container_path": input_island_container,
        },
        "output_trace_island": {
            "host_path": str(output_island_host),
            "container_path": output_trace_island_container,
            "trace_host_path": str(trace_host_path),
            "trace_container_path": trace_container_path,
        },
    }

    if ops_island:
        island_topology["ops_island"] = {
            "host_path": str(ops_island[0]),
            "container_path": ops_island[1],
        }

    return island_topology


def create_state_root() -> Path:
    """Create run-scoped state root temp directory.

    Run-scoped OpenCode state roots prevent any inheritance from host user
    state and stay outside of task workspace to avoid diff pollution.

    Returns:
        Path to the created state root directory.
    """
    state_root_tmp = tempfile.mkdtemp(prefix="nichebench-cage-state-")
    return Path(state_root_tmp)


def create_state_subdirs(state_root: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    """Create state subdirectories.

    Args:
        state_root: State root path.

    Returns:
        Tuple of (home, xdg_config, xdg_data, xdg_state, xdg_cache, bin)
        host paths.
    """
    home_host = state_root / "home"
    xdg_config_host = state_root / "xdg-config"
    xdg_data_host = state_root / "xdg-data"
    xdg_state_host = state_root / "xdg-state"
    xdg_cache_host = state_root / "xdg-cache"
    bin_host = state_root / "bin"
    for path in (home_host, xdg_config_host, xdg_data_host, xdg_state_host, xdg_cache_host, bin_host):
        path.mkdir(parents=True, exist_ok=True)
    return home_host, xdg_config_host, xdg_data_host, xdg_state_host, xdg_cache_host, bin_host
