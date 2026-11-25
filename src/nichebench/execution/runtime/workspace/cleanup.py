"""Cleanup helpers for runtime workspace lifecycle.

Ownership
--------
This module is owned by the workspace package.  It contains all cleanup-related
helpers that are called from ``Workspace`` methods.  It does NOT own the
``Workspace.cleanup()`` method itself (that lives in ``model.py``).

Side-effect boundaries
---------------------
- Calls Docker to remove stale harness containers and networks.
- Logs all operations to the provided ``command_log`` list; never raises.
- Does NOT own DDEV teardown commands (those are in ``ddev.py``).
"""

import subprocess
from pathlib import Path
from typing import Optional

# Docker container statuses that are safe to remove: the container is not
# running and does not belong to an active parallel run.
REMOVABLE_CONTAINER_STATUSES = frozenset({"exited", "created", "dead"})


def _parse_docker_ps_entries(stdout: str) -> list[tuple[str, str]]:
    """Parse ``docker ps --format '{{.Names}} {{.Status}}`` output.

    Args:
        stdout: Raw stdout from the docker command.

    Returns:
        List of ``(container_name, status)`` tuples.
    """
    entries: list[tuple[str, str]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, _, status = line.partition(" ")
        name = name.strip()
        if not name:
            continue
        entries.append((name, status.strip()))
    return entries


def _is_removable_container_status(status: str) -> bool:
    """Return True when a container status is safe to remove.

    Only stopped/dead/created containers qualify.  ``Up``, ``Paused`` and
    ``Restarting`` containers belong to (possibly running) parallel runs and
    are always skipped.
    """
    token = status.strip().split(" ")[0].lower()
    return token in REMOVABLE_CONTAINER_STATUSES


def _network_container_count(
    path: Path,
    network_name: str,
    timeout: Optional[int] = None,
) -> Optional[int]:
    """Return the number of containers attached to a network.

    Args:
        path: Workspace path (used as CWD for docker commands).
        network_name: Network to inspect.
        timeout: Optional command timeout in seconds.

    Returns:
        The attached container count, or ``None`` when the count cannot be
        determined (inspect failed or returned an unparsable value).
    """
    try:
        result = subprocess.run(
            ["docker", "network", "inspect", "-f", "{{len .Containers}}", network_name],
            cwd=path,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except (TypeError, ValueError):
        return None


def cleanup_stale_harness_containers(
    path: Path,
    ddev_project_name: str,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> None:
    """Remove stale ``ddev-nb-*`` containers from previous harness runs.

    Containers belonging to the current project are never touched, and
    containers that are running (``Up``), ``Paused`` or ``Restarting`` are
    always skipped so that parallel runs are not disrupted.

    Args:
        path: Workspace path (used as CWD for docker commands).
        ddev_project_name: Current DDEV project name (containers with this prefix are skipped).
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.
    """
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}} {{.Status}}"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        current_prefix = f"ddev-{ddev_project_name}-"
        entries = _parse_docker_ps_entries(result.stdout)
        stale_containers = [
            name
            for name, status in entries
            if name.startswith("ddev-nb-")
            and not name.startswith(current_prefix)
            and _is_removable_container_status(status)
        ]
        for container_name in stale_containers:
            rm_result = subprocess.run(
                ["docker", "rm", "-f", container_name],
                cwd=path,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            command_log.append(
                {
                    "command": f"docker rm -f {container_name}",
                    "returncode": rm_result.returncode,
                    "stdout": rm_result.stdout,
                    "stderr": rm_result.stderr,
                }
            )
    except Exception as exc:
        command_log.append(
            {
                "command": "docker rm -f <stale harness containers>",
                "warning": "Stale harness container cleanup failed",
                "error": str(exc),
            }
        )


def cleanup_stale_harness_networks(
    path: Path,
    ddev_project_name: str,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> None:
    """Remove stale ``ddev-nb-*_default`` networks from previous harness runs.

    A network is only removed when it does not belong to the current project
    and has zero attached containers.  Networks that cannot be inspected are
    left untouched.

    Args:
        path: Workspace path (used as CWD for docker commands).
        ddev_project_name: Current DDEV project name (current project network is skipped).
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.
    """
    try:
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        current_default = f"ddev-{ddev_project_name}_default"
        network_names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        stale_networks = [
            name
            for name in network_names
            if name.startswith("ddev-nb-") and name.endswith("_default") and name != current_default
        ]
        for network_name in stale_networks:
            attached = _network_container_count(path, network_name, timeout=timeout)
            if attached is None:
                command_log.append(
                    {
                        "command": f"docker network rm {network_name}",
                        "skipped": True,
                        "reason": "Network inspect failed; leaving network in place",
                    }
                )
                continue
            if attached != 0:
                command_log.append(
                    {
                        "command": f"docker network rm {network_name}",
                        "skipped": True,
                        "reason": f"Network has {attached} attached container(s); leaving network in place",
                    }
                )
                continue
            rm_result = subprocess.run(
                ["docker", "network", "rm", network_name],
                cwd=path,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            command_log.append(
                {
                    "command": f"docker network rm {network_name}",
                    "returncode": rm_result.returncode,
                    "stdout": rm_result.stdout,
                    "stderr": rm_result.stderr,
                }
            )
    except Exception as exc:
        command_log.append(
            {
                "command": "docker network rm <stale harness networks>",
                "warning": "Stale harness network cleanup failed",
                "error": str(exc),
            }
        )


def run_docker_network_prune(
    path: Path,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> None:
    """Prune unused docker networks and record command output.

    Args:
        path: Workspace path (used as CWD for docker commands).
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.
    """
    try:
        result = subprocess.run(
            ["docker", "network", "prune", "-f"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        command_log.append(
            {
                "command": "docker network prune -f",
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
    except Exception as exc:
        command_log.append(
            {
                "command": "docker network prune -f",
                "warning": "Docker network prune failed",
                "error": str(exc),
            }
        )


def best_effort_network_hygiene(
    path: Path,
    ddev_project_name: str,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> None:
    """Reduce docker network pressure without raising.

    Calls cleanup helpers for stale containers and networks.  Failures are
    logged but do not raise, preserving cleanup guarantees.

    An empty ``ddev_project_name`` is refused: without a project name every
    ``ddev-nb-*`` resource looks stale, which would tear down containers and
    networks belonging to concurrent parallel runs.

    Args:
        path: Workspace path (used as CWD for docker commands).
        ddev_project_name: Current DDEV project name.
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.
    """
    if not ddev_project_name:
        command_log.append(
            {
                "command": "best_effort_network_hygiene",
                "warning": "Skipped network hygiene: empty DDEV project name",
            }
        )
        return
    cleanup_stale_harness_containers(path, ddev_project_name, command_log, timeout)
    cleanup_stale_harness_networks(path, ddev_project_name, command_log, timeout)


def run_cleanup_command(
    command: list[str],
    path: Path,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
    """Run a cleanup command and log without raising on failure.

    Args:
        command: Command list to execute.
        path: Workspace path (used as CWD).
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.

    Returns:
        CompletedProcess with returncode, stdout, stderr.
    """
    try:
        result = subprocess.run(
            command,
            cwd=path,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        entry: dict[str, object] = {
            "command": " ".join(command),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if result.returncode != 0:
            entry["warning"] = "Cleanup command failed"
        command_log.append(entry)
        return result
    except Exception as exc:
        entry = {
            "command": " ".join(command),
            "warning": "Cleanup command raised exception",
            "error": str(exc),
        }
        command_log.append(entry)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=str(exc))
