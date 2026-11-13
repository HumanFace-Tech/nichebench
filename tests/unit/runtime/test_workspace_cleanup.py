"""Concurrency-safety tests for stale harness container/network cleanup.

Cleanup helpers must never touch resources that belong to another
(concurrently running) DDEV project.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

from nichebench.execution.runtime.workspace import cleanup

CURRENT_PROJECT = "nb-current-abc12345"


def _cp(
    command: list[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class FakeDocker:
    """Minimal docker CLI simulator for cleanup helpers."""

    def __init__(
        self,
        containers: list[str] | None = None,
        networks: list[str] | None = None,
        network_counts: dict[str, int] | None = None,
        inspect_failures: set[str] | None = None,
    ):
        self.containers = list(containers or [])
        self.networks = list(networks or [])
        self.network_counts = dict(network_counts or {})
        self.inspect_failures = set(inspect_failures or [])
        self.commands: list[list[str]] = []

    def run(self, command, **kwargs):  # noqa: ANN001, ANN003, ARG002
        self.commands.append(list(command))
        if command[0] != "docker":
            raise AssertionError(f"unexpected command: {command}")
        action = command[1]
        if action == "ps":
            return _cp(command, stdout=self._lines(self.containers))
        if action == "rm":
            return _cp(command, stdout=command[2])
        if action == "network":
            sub = command[2]
            if sub == "ls":
                return _cp(command, stdout=self._lines(self.networks))
            if sub == "inspect":
                name = command[-1]
                if name in self.inspect_failures:
                    return _cp(command, returncode=1, stderr=f"network {name} not found")
                return _cp(command, stdout=f"{self.network_counts.get(name, 0)}\n")
            if sub == "rm":
                return _cp(command, stdout=command[3])
        raise AssertionError(f"unexpected command: {command}")

    @staticmethod
    def _lines(items: list[str]) -> str:
        return "\n".join(items) + ("\n" if items else "")

    def removed_containers(self) -> list[str]:
        return [c[3] for c in self.commands if c[:2] == ["docker", "rm"]]

    def removed_networks(self) -> list[str]:
        return [c[3] for c in self.commands if c[:2] == ["docker", "network"] and c[2] == "rm"]


def _cleanup_containers(docker: FakeDocker, tmp_path: Path) -> list[dict[str, object]]:
    command_log: list[dict[str, object]] = []
    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=docker.run):
        cleanup.cleanup_stale_harness_containers(tmp_path, CURRENT_PROJECT, command_log)
    return command_log


def _cleanup_networks(docker: FakeDocker, tmp_path: Path) -> list[dict[str, object]]:
    command_log: list[dict[str, object]] = []
    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=docker.run):
        cleanup.cleanup_stale_harness_networks(tmp_path, CURRENT_PROJECT, command_log)
    return command_log


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------


def test_running_and_paused_other_project_containers_are_never_removed(tmp_path: Path):
    docker = FakeDocker(
        containers=[
            "ddev-nb-other-11111111-web Up 5 minutes",
            "ddev-nb-other-11111111-db Paused",
            "ddev-nb-other-11111111-redis Restarting (1) 5 seconds ago",
        ]
    )

    _cleanup_containers(docker, tmp_path)

    assert docker.removed_containers() == []


def test_stopped_other_project_containers_are_removed(tmp_path: Path):
    docker = FakeDocker(
        containers=[
            "ddev-nb-other-11111111-web Exited (0) 2 minutes ago",
            "ddev-nb-other-11111111-db Created",
            "ddev-nb-other-11111111-mail Dead",
        ]
    )

    command_log = _cleanup_containers(docker, tmp_path)

    assert sorted(docker.removed_containers()) == [
        "ddev-nb-other-11111111-db",
        "ddev-nb-other-11111111-mail",
        "ddev-nb-other-11111111-web",
    ]
    assert len(command_log) == 3
    assert all(entry.get("returncode") == 0 for entry in command_log)


def test_current_project_containers_are_never_removed(tmp_path: Path):
    docker = FakeDocker(
        containers=[
            f"ddev-{CURRENT_PROJECT}-web Exited (0) 1 hour ago",
            f"ddev-{CURRENT_PROJECT}-db Created",
        ]
    )

    _cleanup_containers(docker, tmp_path)

    assert docker.removed_containers() == []


def test_non_harness_containers_are_ignored(tmp_path: Path):
    docker = FakeDocker(
        containers=[
            "some-other-container Exited (0) 2 minutes ago",
            "ddev-not-nb-thing-web Exited (0) 2 minutes ago",
        ]
    )

    _cleanup_containers(docker, tmp_path)

    assert docker.removed_containers() == []


def test_container_cleanup_never_raises_when_docker_is_unavailable(tmp_path: Path):
    def boom(command, **kwargs):  # noqa: ANN001, ANN003, ARG002
        raise RuntimeError("docker socket unavailable")

    command_log: list[dict[str, object]] = []
    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=boom):
        cleanup.cleanup_stale_harness_containers(tmp_path, CURRENT_PROJECT, command_log)

    assert any(entry.get("warning") == "Stale harness container cleanup failed" for entry in command_log)


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------


def test_network_with_attached_containers_is_kept(tmp_path: Path):
    docker = FakeDocker(
        networks=["ddev-nb-other-11111111_default"],
        network_counts={"ddev-nb-other-11111111_default": 3},
    )

    _cleanup_networks(docker, tmp_path)

    assert docker.removed_networks() == []


def test_empty_other_project_network_is_removed(tmp_path: Path):
    docker = FakeDocker(
        networks=["ddev-nb-other-11111111_default"],
        network_counts={"ddev-nb-other-11111111_default": 0},
    )

    command_log = _cleanup_networks(docker, tmp_path)

    assert docker.removed_networks() == ["ddev-nb-other-11111111_default"]
    assert command_log[0].get("returncode") == 0


def test_current_project_network_is_kept(tmp_path: Path):
    docker = FakeDocker(
        networks=[f"ddev-{CURRENT_PROJECT}_default"],
        network_counts={f"ddev-{CURRENT_PROJECT}_default": 0},
    )

    _cleanup_networks(docker, tmp_path)

    assert docker.removed_networks() == []


def test_network_is_kept_when_inspect_fails(tmp_path: Path):
    docker = FakeDocker(
        networks=["ddev-nb-other-11111111_default"],
        inspect_failures={"ddev-nb-other-11111111_default"},
    )

    command_log = _cleanup_networks(docker, tmp_path)

    assert docker.removed_networks() == []
    assert any(entry.get("skipped") is True for entry in command_log)


def test_network_cleanup_never_raises_when_docker_is_unavailable(tmp_path: Path):
    def boom(command, **kwargs):  # noqa: ANN001, ANN003, ARG002
        raise RuntimeError("docker socket unavailable")

    command_log: list[dict[str, object]] = []
    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=boom):
        cleanup.cleanup_stale_harness_networks(tmp_path, CURRENT_PROJECT, command_log)

    assert any(entry.get("warning") == "Stale harness network cleanup failed" for entry in command_log)


# ---------------------------------------------------------------------------
# Hygiene entry point
# ---------------------------------------------------------------------------


def test_best_effort_network_hygiene_is_skipped_without_project_name(tmp_path: Path):
    docker = FakeDocker(containers=["ddev-nb-other-11111111-web Exited (0) 2 minutes ago"])

    command_log: list[dict[str, object]] = []
    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=docker.run):
        cleanup.best_effort_network_hygiene(tmp_path, "", command_log)

    assert docker.commands == []
    assert any(entry.get("warning") for entry in command_log)


def test_best_effort_network_hygiene_scopes_cleanup_to_current_project(tmp_path: Path):
    docker = FakeDocker(
        containers=[
            f"ddev-{CURRENT_PROJECT}-web Exited (0) 2 minutes ago",
            "ddev-nb-other-11111111-web Exited (0) 2 minutes ago",
        ],
        networks=[
            f"ddev-{CURRENT_PROJECT}_default",
            "ddev-nb-other-11111111_default",
        ],
        network_counts={"ddev-nb-other-11111111_default": 0},
    )

    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=docker.run):
        cleanup.best_effort_network_hygiene(tmp_path, CURRENT_PROJECT, [])

    assert docker.removed_containers() == ["ddev-nb-other-11111111-web"]
    assert docker.removed_networks() == ["ddev-nb-other-11111111_default"]
