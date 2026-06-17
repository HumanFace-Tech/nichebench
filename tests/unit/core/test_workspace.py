import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nichebench.execution.runtime.workspace import DDEVError, Workspace


def _cp(
    command: list[str],
    returncode: int = 0,
    stdout: str = "ok",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_ddev_start_retries_once_on_subnet_exhaustion(tmp_path: Path):
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    start_calls = 0

    def run_logged(command, path=None, command_log=None, timeout=None):
        nonlocal start_calls
        if command[:2] == ["ddev", "start"]:
            start_calls += 1
            if start_calls == 1:
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd=command,
                    stderr="all predefined address pools have been fully subnetted",
                )
        return _cp(command)

    with (
        patch("nichebench.execution.runtime.workspace.ddev.run_logged_command", side_effect=run_logged),
        patch("nichebench.execution.runtime.workspace.cleanup.best_effort_network_hygiene") as hygiene_mock,
    ):
        workspace.ddev_start(setup_mode="db_snapshot")

    assert start_calls == 2
    assert hygiene_mock.call_count == 2


def test_ddev_start_does_not_retry_on_non_subnet_errors(tmp_path: Path):
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    def run_logged(command, path=None, command_log=None, timeout=None):
        if command[:2] == ["ddev", "start"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr="docker out of memory")
        return _cp(command)

    with (
        patch("nichebench.execution.runtime.workspace.ddev.run_logged_command", side_effect=run_logged),
        patch("nichebench.execution.runtime.workspace.cleanup.best_effort_network_hygiene"),
        pytest.raises(DDEVError),
    ):
        workspace.ddev_start(setup_mode="db_snapshot")


def test_network_pool_error_detector_checks_stderr_and_stdout():
    msg = "all predefined address pools have been fully subnetted"
    exc_stderr = subprocess.CalledProcessError(1, ["ddev", "start"], stderr=msg)
    exc_stdout = subprocess.CalledProcessError(1, ["ddev", "start"], output=msg)
    exc_other = subprocess.CalledProcessError(1, ["ddev", "start"], stderr="permission denied")

    assert Workspace._is_network_pool_exhaustion_error(exc_stderr)
    assert Workspace._is_network_pool_exhaustion_error(exc_stdout)
    assert not Workspace._is_network_pool_exhaustion_error(exc_other)


def test_cleanup_fallback_runs_stop_when_delete_returns_nonzero(tmp_path: Path):
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    called_commands: list[str] = []

    def mock_run(command, **kwargs):
        called_commands.append(" ".join(command))
        if command[:2] == ["ddev", "delete"]:
            return _cp(command, 1, stdout="", stderr="delete failed")
        return _cp(command)

    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=mock_run):
        workspace.cleanup(timeout=10)

    assert any(c.startswith("ddev delete --omit-snapshot -y") for c in called_commands)
    assert any(c.startswith("ddev stop --remove-data -y") for c in called_commands)
    assert "ddev poweroff" not in called_commands
    assert any(
        str(entry.get("command", "")).startswith("ddev delete --omit-snapshot -y") and entry.get("returncode") == 1
        for entry in workspace.command_log
    )
    assert any(
        str(entry.get("command", "")).startswith("ddev stop --remove-data -y") and entry.get("returncode") == 0
        for entry in workspace.command_log
    )
    assert not workspace.path.exists()


def test_cleanup_does_not_run_fallback_when_delete_succeeds(tmp_path: Path):
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    called_commands: list[str] = []

    def mock_run(command, **kwargs):
        called_commands.append(" ".join(command))
        return _cp(command)

    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=mock_run):
        workspace.cleanup(timeout=10)

    assert len(called_commands) == 1
    assert called_commands[0].startswith("ddev delete --omit-snapshot -y")
    assert not workspace.path.exists()


def test_cleanup_never_raises_if_subprocess_run_raises(tmp_path: Path):
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    def mock_run(command, **kwargs):
        raise RuntimeError("docker socket unavailable")

    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=mock_run):
        workspace.cleanup(timeout=10)

    assert not workspace.path.exists()
    assert any(entry.get("warning") == "Cleanup command raised exception" for entry in workspace.command_log)


def test_cleanup_never_raises_if_rmtree_fails(tmp_path: Path):
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    with (
        patch(
            "nichebench.execution.runtime.workspace.cleanup.subprocess.run",
            return_value=_cp(["ddev", "delete", "--omit-snapshot", "-y"]),
        ),
        patch("nichebench.execution.runtime.workspace.model.shutil.rmtree", side_effect=OSError("cannot remove")),
    ):
        workspace.cleanup(timeout=10)

    assert any(entry.get("warning") == "Workspace directory removal failed" for entry in workspace.command_log)


def test_cleanup_can_preserve_workspace_files_but_release_ddev_resources(
    tmp_path: Path,
):
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    called_commands: list[str] = []

    def mock_run(command, **kwargs):
        called_commands.append(" ".join(command))
        return _cp(command)

    with patch("nichebench.execution.runtime.workspace.cleanup.subprocess.run", side_effect=mock_run):
        workspace.cleanup(timeout=10, remove_workspace=False)

    assert any(c.startswith("ddev delete --omit-snapshot -y") for c in called_commands)
    assert workspace.path.exists()


# ---------------------------------------------------------------------------
# AGENTS.md copy behavior
# ---------------------------------------------------------------------------


def test_ensure_agents_md_copies_when_agents_md_absent(tmp_path: Path):
    """When AGENTS.md is absent and AGENTS.mut.md exists, AGENTS.md is created as a copy."""
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    mut_content = "# MUT Agent Instructions\n\nDo stuff."
    (workspace.path / "AGENTS.mut.md").write_text(mut_content, encoding="utf-8")

    workspace._ensure_agents_md()

    agents_md = workspace.path / "AGENTS.md"
    assert agents_md.exists(), "AGENTS.md should have been created"
    assert agents_md.read_text(encoding="utf-8") == mut_content


def test_ensure_agents_md_noop_when_agents_md_already_exists(tmp_path: Path):
    """When AGENTS.md already exists, it is NOT overwritten."""
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    original_content = "# Existing AGENTS.md\n\nOriginal content."
    (workspace.path / "AGENTS.md").write_text(original_content, encoding="utf-8")
    (workspace.path / "AGENTS.mut.md").write_text("# MUT content", encoding="utf-8")

    workspace._ensure_agents_md()

    agents_md = workspace.path / "AGENTS.md"
    assert agents_md.read_text(encoding="utf-8") == original_content, "AGENTS.md must not be overwritten"


def test_ensure_agents_md_noop_when_agents_mut_md_absent(tmp_path: Path):
    """When AGENTS.mut.md is absent, nothing is created."""
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True)

    workspace._ensure_agents_md()

    assert not (workspace.path / "AGENTS.md").exists(), "AGENTS.md should not be created when AGENTS.mut.md is absent"


def test_ensure_agents_md_called_during_workspace_create(tmp_path: Path):
    """create() calls _ensure_agents_md() after cloning."""
    workspace = Workspace(base_path=tmp_path, task_id="drupal_runtime_001")

    # Simulate a minimal source repo with AGENTS.mut.md but no AGENTS.md
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True, capture_output=True)
    (source / "AGENTS.mut.md").write_text("# MUT Instructions", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "init"], check=True, capture_output=True)

    workspace.create(source_path=source)

    agents_md = workspace.path / "AGENTS.md"
    assert agents_md.exists(), "create() should have copied AGENTS.mut.md to AGENTS.md"
    assert agents_md.read_text(encoding="utf-8") == "# MUT Instructions"
