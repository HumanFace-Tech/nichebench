"""Docker command construction and socket group handling.

**Ownership**: This module is owned by ``CageExecutionMixin`` (mixin.py). It
contains only docker command assembly and socket group helpers; it does not own
island topology, subprocess handling, or watchdog logic.

**Container safety constraints**:
- Docker socket is mounted with read-only option where possible.
- Container runs with dropped capabilities and no-new-privileges.
- User is set explicitly (not root) to prevent privilege escalation.
- Socket group is added for non-root docker access (best-effort).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# ------------------------------------------------------------------
# Docker socket group helpers
# ------------------------------------------------------------------


def get_docker_socket_gid() -> Optional[int]:
    """Get the GID of the docker socket group.

    Returns:
        GID of /var/run/docker.sock group, or None if not accessible.
    """
    try:
        return os.stat("/var/run/docker.sock").st_gid
    except Exception:
        return None


def build_docker_base_args(
    container_name: str,
    runtime_user: str,
    read_only: bool,
    docker_socket_gid: Optional[int],
) -> List[str]:
    """Build the base docker run arguments.

    Args:
        container_name: Unique container name.
        runtime_user: User spec in UID:GID format.
        read_only: Whether to use read-only container mode.
        docker_socket_gid: GID to add for docker socket access (or None).

    Returns:
        List of docker run argument strings.
    """
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--label",
        "nichebench.role=opencode-cage",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        runtime_user,
    ]
    if docker_socket_gid is not None:
        command.extend(["--group-add", str(docker_socket_gid)])
    return command


def build_docker_volume_args(
    workspace_host_path: Path,
    workspace_container_path: str,
    input_island_host: Path,
    input_island_container: str,
    output_island_host: Path,
    output_trace_island_container: str,
    home_host: Path,
    xdg_config_host: Path,
    xdg_data_host: Path,
    xdg_state_host: Path,
    xdg_cache_host: Path,
    bin_host: Path,
    container_state_root: str,
    island_topology: Dict[str, Any],
    read_only: bool,
) -> List[List[str]]:
    """Build docker volume mount argument groups.

    Args:
        workspace_host_path: Host path for workspace.
        workspace_container_path: Container path for workspace.
        input_island_host: Host path for input island.
        input_island_container: Container path for input island.
        output_island_host: Host path for output island.
        output_trace_island_container: Container path for output/trace island.
        home_host: Host path for home directory.
        xdg_config_host: Host path for XDG config.
        xdg_data_host: Host path for XDG data.
        xdg_state_host: Host path for XDG state.
        xdg_cache_host: Host path for XDG cache.
        bin_host: Host path for bin directory.
        container_state_root: Container path for state root.
        island_topology: Island topology dict (may contain ops_island).
        read_only: Whether to use read-only container mode.

    Returns:
        List of volume argument groups (each group is a list of strings
        ready to extend the command).
    """
    volumes = [
        "-v",
        f"{workspace_host_path}:{workspace_container_path}",
        "-v",
        f"{input_island_host}:{input_island_container}:ro",
        "-v",
        f"{output_island_host}:{output_trace_island_container}",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-w",
        workspace_container_path,
        "-v",
        f"{home_host}:{container_state_root}/home",
        "-v",
        f"{xdg_config_host}:{container_state_root}/xdg-config",
        "-v",
        f"{xdg_data_host}:{container_state_root}/xdg-data",
        "-v",
        f"{xdg_state_host}:{container_state_root}/xdg-state",
        "-v",
        f"{xdg_cache_host}:{container_state_root}/xdg-cache",
        "-v",
        f"{bin_host}:{container_state_root}/bin:ro",
    ]
    if "ops_island" in island_topology:
        volumes.extend(
            [
                "-v",
                (f"{island_topology['ops_island']['host_path']}" f":{island_topology['ops_island']['container_path']}"),
            ]
        )
    if read_only:
        return [volumes, ["--read-only", "--tmpfs", "/tmp", "--tmpfs", "/run"]]
    return [volumes, []]


def build_docker_env_args(
    env: Dict[str, str],
    api_keys: Dict[str, str],
) -> List[List[str]]:
    """Build docker environment variable argument groups.

    Args:
        env: Base environment variables dict.
        api_keys: Provider API keys dict.

    Returns:
        List of environment argument groups.
    """
    result: List[List[str]] = []
    for key, value in env.items():
        result.append(["-e", f"{key}={value}"])
    for key, value in api_keys.items():
        result.append(["-e", f"{key}={value}"])
    return result


def build_opencode_command(
    image: str,
    opencode_model_binding: str,
    task_input: str,
) -> List[str]:
    """Build the OpenCode container entrypoint command.

    Args:
        image: Docker image to use.
        opencode_model_binding: OpenCode model binding string (provider/model_id).
        task_input: Task input string.

    Returns:
        Complete docker command suffix (entrypoint, image, and args).
    """
    return [
        "--entrypoint",
        "opencode",
        image,
        "run",
        "--pure",
        "--dangerously-skip-permissions",
        "--model",
        opencode_model_binding,
        task_input,
    ]


def build_cage_container_name(test_case_id: str, state_root_name: str) -> str:
    """Build a unique cage container name.

    Args:
        test_case_id: Test case identifier.
        state_root_name: State root directory name (for uniqueness).

    Returns:
        Unique container name string.
    """
    return f"nichebench-{test_case_id}-{state_root_name}".replace("_", "-")


def compute_runtime_env(
    test_case_id: str,
    profile_name: str,
    mut_runner: Any,
    island_topology: Dict[str, Any],
) -> Dict[str, str]:
    """Compute the runtime environment variables for the cage container.

    Args:
        test_case_id: Test case identifier.
        profile_name: Profile name string.
        mut_runner: MUT runner instance (used for model config access).
        island_topology: Island topology dict.

    Returns:
        Environment variables dict for the container.
    """
    env = {
        "NB_TASK_ID": test_case_id,
        "NB_TOOL_PROFILE": profile_name,
        "NB_MODEL_PROVIDER": str(mut_runner.model_config.get("provider", "")),
        "NB_MODEL_NAME": str(mut_runner.model_config.get("model", "")),
        "NB_RUNTIME_MODE": "cage",
        "USER": "opencode",
    }

    # Add island env vars from topology
    if "input_island" in island_topology:
        env["NB_ISLAND_INPUT"] = island_topology["input_island"]["container_path"]
    if "output_trace_island" in island_topology:
        env["NB_ISLAND_OUTPUT_TRACE"] = island_topology["output_trace_island"]["container_path"]
        env["NB_ISLAND_OUTPUT"] = island_topology["output_trace_island"]["container_path"]
        env["NB_ISLAND_TRACE"] = island_topology["output_trace_island"]["trace_container_path"]
    if "ops_island" in island_topology:
        env["NB_ISLAND_OPS"] = island_topology["ops_island"]["container_path"]

    return env


def build_state_env(container_state_root: str) -> Dict[str, str]:
    """Build environment variables for run-scoped cage state.

    Args:
        container_state_root: Container path for state root.

    Returns:
        State-related environment variables dict.
    """
    return {
        "HOME": f"{container_state_root}/home",
        "XDG_CONFIG_HOME": f"{container_state_root}/xdg-config",
        "XDG_DATA_HOME": f"{container_state_root}/xdg-data",
        "XDG_STATE_HOME": f"{container_state_root}/xdg-state",
        "XDG_CACHE_HOME": f"{container_state_root}/xdg-cache",
        "PATH": (f"{container_state_root}/bin:" "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
    }


def apply_openai_base_url_env(
    cage_api_base: Optional[str],
    env: Dict[str, str],
) -> None:
    """Apply OpenAI-compatible base URL to env if configured.

    When using a custom OpenAI-compatible endpoint (e.g. llama-swap), pass
    the base URL so OpenCode inside the cage can reach it. OpenCode reads
    OPENAI_BASE_URL (not OPENAI_API_BASE).

    Args:
        cage_api_base: Normalized API base URL (with /v1 suffix), or None.
        env: Environment variables dict to mutate in-place.
    """
    if cage_api_base:
        env["OPENAI_BASE_URL"] = cage_api_base
        env["OPENAI_API_KEY"] = "dummy"
