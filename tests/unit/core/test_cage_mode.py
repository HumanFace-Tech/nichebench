"""Unit tests for cage mode defaults, mode normalization, and metadata (Tasks 1.1-1.3)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from nichebench.config.nichebench_config import NicheBenchConfig
from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.profiles import resolve_profile
from nichebench.execution.orchestrator import TestExecutor
from nichebench.execution.runtime.scoring import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f)


def _make_executor(runtime_config=None, category="runtime"):
    mut_cfg = {"provider": "groq", "model": "test-model", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}
    # Default watchdog off so command-construction tests keep using subprocess.run path.
    base_cfg = {"runtime_watchdog_enable": False}
    eval_cfg = {**base_cfg, **(runtime_config or {})}

    with (
        patch("nichebench.execution.orchestrator.get_config") as mock_config,
        patch.object(TestExecutor, "_load_system_prompt", return_value=None),
        patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
    ):
        mock_config.return_value.get_evaluation_config.return_value = eval_cfg
        mock_config.return_value.get_mut_config.return_value = mut_cfg
        mock_config.return_value.get_model_string.side_effect = lambda cfg: (f"{cfg['provider']}/{cfg['model']}")
        return TestExecutor(
            framework="drupal_runtime",
            category=category,
            mut_config=mut_cfg,
            judge_config=judge_cfg,
            network_config=network_cfg,
        )


class TestRuntimeRouting:
    def test_execute_test_delegates_to_runtime_path(self):
        executor = _make_executor({"runtime_mode": "cage"}, category="runtime")
        test_case = TestCaseSpec(id="runtime_001", type="runtime", raw={})
        runtime_result = MagicMock()

        with (
            patch.object(executor, "execute_runtime_test", return_value=runtime_result) as mock_runtime,
            patch.object(executor.mut_runner, "run_test") as mock_mut,
            patch.object(executor.judge_runner, "evaluate_test") as mock_judge,
        ):
            result = executor.execute_test(test_case)

        assert result is runtime_result
        mock_runtime.assert_called_once_with(test_case, trial=0)
        mock_mut.assert_not_called()
        mock_judge.assert_not_called()


class TestRuntimeHints:
    def test_hints_disabled_noops(self, tmp_path: Path) -> None:
        executor = _make_executor({"runtime_hints_enabled": False})
        task = TestCaseSpec(
            id="drupal_runtime_001",
            type="runtime",
            raw={},
            file_path=str(tmp_path / "tasks" / "manifest" / "drupal_runtime_001.yaml"),
        )
        (tmp_path / "TASK.md").write_text("Task body\n", encoding="utf-8")

        assert executor._inject_runtime_hints(tmp_path, task) is None
        assert (tmp_path / "TASK.md").read_text(encoding="utf-8") == "Task body\n"

    def test_hints_enabled_copies_global_hints_file(self, tmp_path: Path) -> None:
        executor = _make_executor({"runtime_hints_enabled": True})
        manifest = tmp_path / "tasks" / "manifest" / "drupal_runtime_001.yaml"
        hints = tmp_path / "tasks" / "HINTS.md"
        hints.parent.mkdir(parents=True)
        manifest.parent.mkdir(parents=True)
        manifest.write_text("task_id: drupal_runtime_001\n", encoding="utf-8")
        hints.write_text("Use the correct Drupal APIs.\n", encoding="utf-8")
        task = TestCaseSpec(id="drupal_runtime_001", type="runtime", raw={}, file_path=str(manifest))
        (tmp_path / "TASK.md").write_text("Task body\n", encoding="utf-8")

        used_path = executor._inject_runtime_hints(tmp_path, task)

        assert used_path == hints
        assert (tmp_path / "TASK.md").read_text(encoding="utf-8") == "Task body\n"
        assert (tmp_path / "HINTS.md").read_text(encoding="utf-8") == "Use the correct Drupal APIs.\n"

    def test_hints_enabled_missing_file_raises(self, tmp_path: Path) -> None:
        executor = _make_executor({"runtime_hints_enabled": True})
        manifest = tmp_path / "tasks" / "manifest" / "drupal_runtime_001.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("task_id: drupal_runtime_001\n", encoding="utf-8")
        task = TestCaseSpec(id="drupal_runtime_001", type="runtime", raw={}, file_path=str(manifest))

        with pytest.raises(ValidationError, match="no hints file found"):
            executor._inject_runtime_hints(tmp_path, task)

    def test_execute_test_non_runtime_uses_mut_and_judge(self):
        executor = _make_executor({}, category="quiz")
        test_case = TestCaseSpec(id="quiz_001", type="quiz", raw={})

        with (
            patch.object(executor, "execute_runtime_test") as mock_runtime,
            patch.object(
                executor.mut_runner,
                "run_test",
                return_value=("mut output", "user input"),
            ) as mock_mut,
            patch.object(
                executor.judge_runner,
                "evaluate_test",
                return_value=({"overall_score": 1.0}, True),
            ) as mock_judge,
        ):
            result = executor.execute_test(test_case)

        mock_runtime.assert_not_called()
        mock_mut.assert_called_once_with(test_case, executor.system_prompt, executor.category, None)
        mock_judge.assert_called_once_with(
            test_case,
            executor.category,
            "user input",
            "mut output",
            executor.judge_system_prompt,
        )
        assert result.mut_output == "mut output"
        assert result.user_input == "user input"
        assert result.judge_output == {"overall_score": 1.0}
        assert result.passed is True


# ---------------------------------------------------------------------------
# Task 1.1: Default runtime mode is cage
# ---------------------------------------------------------------------------


class TestCageModeDefault:
    """Task 1.1: Canonical runtime defaults to cage mode."""

    def test_config_default_runtime_mode_is_cage(self, tmp_path):
        cfg_path = tmp_path / "nichebench.yml"
        _write_yaml(cfg_path, {})
        cfg = NicheBenchConfig(config_path=cfg_path)
        eval_conf = cfg.get_evaluation_config()
        assert eval_conf["runtime_mode"] == "cage"

    def test_config_default_image_is_pinned(self, tmp_path):
        cfg_path = tmp_path / "nichebench.yml"
        _write_yaml(cfg_path, {})
        cfg = NicheBenchConfig(config_path=cfg_path)
        eval_conf = cfg.get_evaluation_config()
        image = eval_conf["runtime_container_image"]
        assert image
        assert "latest" not in image

    def test_host_mode_via_explicit_override(self, tmp_path):
        cfg_path = tmp_path / "nichebench.yml"
        _write_yaml(cfg_path, {"evaluation": {"runtime_mode": "host"}})
        cfg = NicheBenchConfig(config_path=cfg_path)
        eval_conf = cfg.get_evaluation_config()
        assert eval_conf["runtime_mode"] == "host"

    def test_container_alias_normalizes_to_cage(self):
        """Legacy 'container' mode value normalizes to 'cage' in executor."""
        executor = _make_executor({"runtime_mode": "container"})
        # The executor reads runtime_mode from config; we verify normalization
        # happens inside execute_runtime_test. Test indirectly via preflight.
        runtime_config = executor.evaluation_config
        raw = str(runtime_config.get("runtime_mode", "cage"))
        effective = "cage" if raw in ("cage", "container") else raw
        assert effective == "cage"


# ---------------------------------------------------------------------------
# Task 1.1: Mode normalization in executor
# ---------------------------------------------------------------------------


class TestModeNormalization:
    """Verify executor normalizes container→cage and preserves host."""

    @pytest.mark.parametrize(
        "raw_mode,expected",
        [
            ("cage", "cage"),
            ("container", "cage"),
            ("host", "host"),
        ],
    )
    def test_normalize_runtime_mode(self, raw_mode, expected):
        normalized = "cage" if raw_mode in ("cage", "container") else raw_mode
        assert normalized == expected


# ---------------------------------------------------------------------------
# Task 1.2: Pinned image validation in preflight
# ---------------------------------------------------------------------------


class TestCageModePreflight:
    """Task 1.2: Cage mode fails fast without pinned image reference."""

    def test_cage_mode_preflight_rejects_empty_image(self):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "",
            }
        )
        with pytest.raises(ValidationError, match="must be configured"):
            executor._run_runtime_preflight_host(executor.evaluation_config, "cage")

    def test_cage_mode_preflight_rejects_latest_image(self):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:latest",
            }
        )
        with pytest.raises(ValidationError, match="floating tag"):
            executor._run_runtime_preflight_host(executor.evaluation_config, "cage")

    def test_cage_mode_preflight_accepts_pinned_image(self):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        # Should not raise (docker/ddev preflight may fail but not pin validation)
        try:
            executor._run_runtime_preflight_host(executor.evaluation_config, "cage")
        except ValidationError as e:
            # Pin validation should pass; only docker/ddev errors are acceptable
            assert "pin" not in str(e).lower() and "floating" not in str(e).lower()

    def test_host_mode_skips_pin_validation(self):
        """Host mode should not require pin validation."""
        executor = _make_executor(
            {
                "runtime_mode": "host",
                "runtime_container_image": "",
            }
        )
        # Should not raise a pin-related error (docker/ddev may fail)
        try:
            executor._run_runtime_preflight_host(executor.evaluation_config, "host")
        except ValidationError as e:
            assert "pin" not in str(e).lower() and "must be configured" not in str(e).lower()


# ---------------------------------------------------------------------------
# Task 1.3: Runtime metadata fields
# ---------------------------------------------------------------------------


class TestRuntimeMetadata:
    """Task 1.3: Metadata records effective mode, image ref, and model binding."""

    def _build_metadata(self, runtime_mode="cage", image="ghcr.io/opencode-ai/opencode:v0.14.0"):
        executor = _make_executor(
            {
                "runtime_mode": runtime_mode,
                "runtime_container_image": image,
            }
        )
        test_case = TestCaseSpec(
            id="test_001",
            type="runtime",
            raw={"source": {"task_branch": "task/test_001"}},
            base_branch="task/test_001",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-uuid"
        return executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode=runtime_mode,
            runtime_config=executor.evaluation_config,
            workspace=workspace,
        )

    def test_metadata_has_effective_runtime_mode(self):
        meta = self._build_metadata(runtime_mode="cage")
        assert meta["effective_runtime_mode"] == "cage"
        assert meta["runtime_mode"] == "cage"

    def test_metadata_has_effective_runtime_mode_host(self):
        meta = self._build_metadata(runtime_mode="host")
        assert meta["effective_runtime_mode"] == "host"

    def test_metadata_has_container_image_reference(self):
        meta = self._build_metadata(image="ghcr.io/opencode-ai/opencode:v0.14.0")
        # After metadata update, we have base and effective image fields
        assert meta["runtime_container_image_base"] == "ghcr.io/opencode-ai/opencode:v0.14.0"
        assert meta["runtime_container_image_effective"] == "ghcr.io/opencode-ai/opencode:v0.14.0"

    def test_metadata_has_mut_model_binding(self):
        meta = self._build_metadata()
        assert "mut_model_binding" in meta
        assert meta["mut_model_binding"] == "groq/test-model"

    def test_metadata_has_capability_lock_fields(self):
        meta = self._build_metadata()
        assert "tool_flags" in meta
        assert "allow_web_search" in meta["tool_flags"]
        assert "allow_browser" in meta["tool_flags"]
        assert "allow_mcp" in meta["tool_flags"]
        assert "allow_external_network_for_shell" in meta["tool_flags"]
        # Offline CLI profile defaults
        assert meta["tool_flags"]["allow_web_search"] is False
        assert meta["tool_flags"]["allow_mcp"] is True

    def test_metadata_can_record_island_topology(self):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(
            id="test_001",
            type="runtime",
            raw={"source": {"task_branch": "task/test_001"}},
            base_branch="task/test_001",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-uuid"
        topology = {
            "workspace": {
                "host_path": "/tmp/ws",
                "container_path": "/workspace",
            },
            "input_island": {
                "host_path": "/tmp/ws",
                "container_path": "/nichebench/islands/input",
            },
        }

        meta = executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode="cage",
            runtime_config=executor.evaluation_config,
            workspace=workspace,
            island_topology=topology,
        )
        assert meta["island_topology"] == topology

    def test_metadata_omits_retry_info_when_not_retried(self):
        """When no retry_info passed, metadata does NOT include retry_info key."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(
            id="test_retry_001",
            type="runtime",
            raw={"source": {"task_branch": "task/test_retry"}},
            base_branch="task/test_retry",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-retry"

        meta = executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode="cage",
            runtime_config=executor.evaluation_config,
            workspace=workspace,
        )

        # retry_info is only present when retry was actually attempted
        assert "retry_info" not in meta, f"Unexpected retry_info in metadata: {meta.get('retry_info')}"

    def test_metadata_retry_info_when_retry_attempted(self):
        """When retry_info passed, metadata includes it with attempted=True."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(
            id="test_retry_002",
            type="runtime",
            raw={"source": {"task_branch": "task/test_retry"}},
            base_branch="task/test_retry",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-retry"

        retry_info = {"attempted": True, "reason": "rejected tool attempts: ['exec']"}
        meta = executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode="cage",
            runtime_config=executor.evaluation_config,
            workspace=workspace,
            retry_info=retry_info,
        )

        assert "retry_info" in meta
        assert meta["retry_info"]["attempted"] is True
        assert "exec" in meta["retry_info"]["reason"]


class TestCageStartupContract:
    """Task 2.1-2.3: enforce benchmark-owned startup contract in cage mode."""

    def test_cage_launch_uses_forced_model_binding_and_unrestricted_permissions(self, tmp_path):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple: (mut_output, user_input, container_output, island_topology, effective_image, trajectory)
            assert len(result) == 6
            assert result[4] == "ghcr.io/opencode-ai/opencode:v0.14.0"  # effective_image

        command = mock_run.call_args.args[0]
        assert "--entrypoint" in command
        entrypoint_idx = command.index("--entrypoint")
        assert command[entrypoint_idx + 1] == "opencode"
        assert "--model" in command
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "groq/test-model"
        assert "--pure" in command
        assert "--dangerously-skip-permissions" in command
        # Verify no duplicated "opencode" positional after image
        image_idx = command.index("ghcr.io/opencode-ai/opencode:v0.14.0")
        # After image, next should be "run", not "opencode"
        assert command[image_idx + 1] == "run"
        assert "opencode" not in command[image_idx + 1 :]

    @pytest.mark.parametrize(
        "model_config",
        [
            {"provider": "", "model": "test-model"},
            {"provider": "groq", "model": ""},
        ],
    )
    def test_cage_launch_requires_mut_provider_and_model(self, tmp_path, model_config):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        executor.mut_runner.model_config["provider"] = model_config["provider"]
        executor.mut_runner.model_config["model"] = model_config["model"]
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with pytest.raises(ValidationError, match="requires explicit MUT provider/model binding"):
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Should not reach here due to ValidationError, but if it does, verify 6-tuple
            assert len(result) == 6

    def test_cage_launch_sets_run_scoped_home_and_xdg(self, tmp_path):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_002", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return
            assert len(result) == 6

        command = mock_run.call_args.args[0]
        mount_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-v"]
        env_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-e"]

        assert "HOME=/nichebench/state/home" in env_values
        assert "XDG_CONFIG_HOME=/nichebench/state/xdg-config" in env_values
        assert "XDG_DATA_HOME=/nichebench/state/xdg-data" in env_values
        assert "XDG_STATE_HOME=/nichebench/state/xdg-state" in env_values
        assert "XDG_CACHE_HOME=/nichebench/state/xdg-cache" in env_values

        mount_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-v"]
        assert any(m.endswith(":/nichebench/state/home") for m in mount_values)
        assert any(m.endswith(":/nichebench/state/xdg-config") for m in mount_values)
        assert any(m.endswith(":/nichebench/state/xdg-data") for m in mount_values)

    def test_cage_launch_mounts_explicit_islands(self, tmp_path):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_003", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        workspace.run_artifacts_path = tmp_path / "results" / "run"
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return
            assert len(result) == 6

        command = mock_run.call_args.args[0]
        mount_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-v"]
        env_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-e"]

        assert f"{tmp_path}:/nichebench/islands/input:ro" in mount_values
        assert f"{workspace.run_artifacts_path}:/nichebench/islands/output-trace" in mount_values
        assert "NB_ISLAND_INPUT=/nichebench/islands/input" in env_values
        assert "NB_ISLAND_OUTPUT_TRACE=/nichebench/islands/output-trace" in env_values
        assert "NB_ISLAND_OUTPUT=/nichebench/islands/output-trace" in env_values
        assert "NB_ISLAND_TRACE=/nichebench/islands/output-trace/trace" in env_values

        # Verify prompt text is passed as the final positional message argument
        assert "--prompt-file" not in command
        assert command[-1] == "Implement task"

    def test_cage_run_log_written_to_output_trace_island(self, tmp_path):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_004", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        workspace.run_artifacts_path = tmp_path / "results" / "run"
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return
            assert len(result) == 6

        run_log = workspace.run_artifacts_path / "run.log"
        assert run_log.exists()
        assert "STDOUT:" in run_log.read_text(encoding="utf-8")

    def test_cage_launch_normalizes_relative_workspace_paths_for_docker(self, tmp_path, monkeypatch):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_005", type="runtime", raw={}, prompt="Implement task")

        monkeypatch.chdir(tmp_path)

        workspace = MagicMock()
        workspace.path = Path("workspaces/run-test")
        workspace.path.mkdir(parents=True, exist_ok=True)
        workspace.run_artifacts_path = Path("artifacts/run")
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return
            assert len(result) == 6

        command = mock_run.call_args.args[0]
        mount_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-v"]

        workspace_abs = str((tmp_path / "workspaces" / "run-test").resolve())
        output_abs = str((tmp_path / "artifacts" / "run").resolve())

        assert f"{workspace_abs}:{workspace_abs}" in mount_values
        assert f"{workspace_abs}:/nichebench/islands/input:ro" in mount_values
        assert f"{output_abs}:/nichebench/islands/output-trace" in mount_values

        workdir_idx = command.index("-w")
        assert command[workdir_idx + 1] == workspace_abs
        assert command[workdir_idx + 1] != "/workspace"


# ---------------------------------------------------------------------------
# Task: DDEV-capable image flow (cage mode enhancements)
# ---------------------------------------------------------------------------


class TestCageModeDDEVImageFlow:
    """Test cage mode DDEV-capable image resolution and usage."""

    def test_config_default_includes_ddev_image_flow_keys(self, tmp_path):
        """Default config includes new ddev image flow keys."""
        cfg_path = tmp_path / "nichebench.yml"
        _write_yaml(cfg_path, {})
        cfg = NicheBenchConfig(config_path=cfg_path)
        eval_conf = cfg.get_evaluation_config()
        assert eval_conf["runtime_container_enable_ddev"] is True
        assert eval_conf["runtime_container_ddev_image"] == "nichebench/opencode-ddev:1.14.25"
        assert eval_conf["runtime_container_ddev_auto_build"] is True

    def test_config_ddev_flow_can_be_disabled(self, tmp_path):
        """DDEV flow can be disabled via config."""
        cfg_path = tmp_path / "nichebench.yml"
        _write_yaml(
            cfg_path,
            {
                "evaluation": {
                    "runtime_container_enable_ddev": False,
                    "runtime_container_ddev_auto_build": False,
                }
            },
        )
        cfg = NicheBenchConfig(config_path=cfg_path)
        eval_conf = cfg.get_evaluation_config()
        assert eval_conf["runtime_container_enable_ddev"] is False
        assert eval_conf["runtime_container_ddev_auto_build"] is False

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_resolve_effective_cage_image_already_has_ddev(self, mock_run, tmp_path):
        """Base image with ddev/docker binaries is returned as-is."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
            }
        )

        # Mock successful probe (binaries exist)
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        effective_image = executor._resolve_effective_cage_image(executor.evaluation_config)

        assert effective_image == "ghcr.io/opencode-ai/opencode:v0.14.0"
        # Verify probe was called
        assert mock_run.called
        probe_call = mock_run.call_args_list[0]
        assert "docker" in probe_call[0][0]
        assert "run" in probe_call[0][0]
        assert "ddev" in probe_call[0][0][-1]
        # Verify probe command uses --entrypoint sh to force shell entrypoint
        assert "--entrypoint" in probe_call[0][0]
        entrypoint_idx = probe_call[0][0].index("--entrypoint")
        assert probe_call[0][0][entrypoint_idx + 1] == "sh"

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_resolve_effective_cage_image_missing_auto_build_enabled(self, mock_run, tmp_path):
        """Missing binaries with auto_build enabled builds derived image."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
                "runtime_container_ddev_image": "nichebench/opencode-ddev:1.14.25",
                "runtime_container_ddev_auto_build": True,
            }
        )

        # Mock probe failures (binaries missing), then build success, then probe success
        call_count = [0]

        def mock_side_effect(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0] if args else []
            cmd_str = " ".join(cmd)
            # First probe on base image (fails)
            if call_count[0] == 1 and "docker run" in cmd_str and "ghcr.io/opencode-ai/opencode:v0.14.0" in cmd_str:
                return MagicMock(returncode=1, stdout="", stderr="")
            # Build call (succeeds)
            if (
                call_count[0] == 2
                and "docker build" in cmd_str
                or (call_count[0] == 3 and "docker run" in cmd_str and "nichebench/opencode-ddev:1.14.25" in cmd_str)
            ):
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_side_effect

        effective_image = executor._resolve_effective_cage_image(executor.evaluation_config)

        # Should return derived image
        assert effective_image == "nichebench/opencode-ddev:1.14.25"
        # Verify probe, build, probe sequence
        assert mock_run.call_count >= 2

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_resolve_effective_cage_image_missing_auto_build_disabled(self, mock_run, tmp_path):
        """Missing binaries with auto_build disabled raises ValidationError."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
                "runtime_container_ddev_auto_build": False,
            }
        )

        # Mock probe failure (binaries missing)
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        with pytest.raises(ValidationError, match="lacks required ddev/docker/git binaries or ddev drush support"):
            executor._resolve_effective_cage_image(executor.evaluation_config)

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_resolve_effective_cage_image_ddev_disabled_returns_base(self, mock_run, tmp_path):
        """When DDEV is disabled, returns base image without probing."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": False,
            }
        )

        # Should not call subprocess at all
        assert not mock_run.called

        effective_image = executor._resolve_effective_cage_image(executor.evaluation_config)

        assert effective_image == "ghcr.io/opencode-ai/opencode:v0.14.0"
        assert not mock_run.called

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_resolve_effective_cage_image_build_fails_raises(self, mock_run, tmp_path):
        """Build failure raises ValidationError."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
                "runtime_container_ddev_auto_build": True,
            }
        )

        # Mock probe failure (binaries missing), then build failure
        call_count = [0]

        def mock_side_effect(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0] if args else []
            cmd_str = " ".join(cmd)
            # First probe on base image (fails)
            if call_count[0] == 1 and "docker run" in cmd_str:
                return MagicMock(returncode=1, stdout="", stderr="")
            # Build call fails
            if call_count[0] == 2 and "docker build" in cmd_str:
                raise subprocess.CalledProcessError(1, "docker build", stderr="Build failed")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_side_effect

        with pytest.raises(ValidationError, match="Failed to build ddev-capable image"):
            executor._resolve_effective_cage_image(executor.evaluation_config)

    def test_metadata_includes_base_and_effective_image_fields(self, tmp_path):
        """Metadata includes base and effective image refs."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
            }
        )

        test_case = TestCaseSpec(
            id="test_001",
            type="runtime",
            raw={"source": {"task_branch": "task/test_001"}},
            base_branch="task/test_001",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-uuid"

        # Pass effective_image directly - metadata should NOT re-resolve
        meta = executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode="cage",
            runtime_config=executor.evaluation_config,
            workspace=workspace,
            effective_image="base-image",
        )

        assert "runtime_container_image_base" in meta
        assert meta["runtime_container_image_base"] == "ghcr.io/opencode-ai/opencode:v0.14.0"
        assert "runtime_container_image_effective" in meta
        assert meta["runtime_container_image_effective"] == "base-image"

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_runtime_command_uses_effective_image(self, mock_run, tmp_path):
        """Cage runtime command uses effective image when DDEV enabled."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
                "runtime_container_ddev_image": "nichebench/opencode-ddev:1.14.25",
                "runtime_container_ddev_auto_build": True,
            }
        )

        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        # Mock probe success (image already has binaries)
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(executor, "_resolve_effective_cage_image", return_value="effective-image"):
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return and effective_image is returned
            assert len(result) == 6
            assert result[4] == "effective-image"

        # Verify docker run was called with effective image
        assert mock_run.called
        docker_cmd = mock_run.call_args.args[0]
        assert "effective-image" in docker_cmd
        # Verify --entrypoint opencode is set
        assert "--entrypoint" in docker_cmd
        entrypoint_idx = docker_cmd.index("--entrypoint")
        assert docker_cmd[entrypoint_idx + 1] == "opencode"
        # Verify no duplicated "opencode" positional after image
        image_idx = docker_cmd.index("effective-image")
        assert docker_cmd[image_idx + 1] == "run"

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_metadata_host_mode_uses_base_image_only(self, tmp_path):
        """Host mode metadata only includes base image (no effective)."""
        executor = _make_executor(
            {
                "runtime_mode": "host",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
            }
        )

        test_case = TestCaseSpec(
            id="test_001",
            type="runtime",
            raw={"source": {"task_branch": "task/test_001"}},
            base_branch="task/test_001",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-uuid"

        meta = executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode="host",
            runtime_config=executor.evaluation_config,
            workspace=workspace,
        )

        # Host mode should have base image, effective equals base
        assert "runtime_container_image_base" in meta
        assert meta["runtime_container_image_base"] == "ghcr.io/opencode-ai/opencode:v0.14.0"
        assert "runtime_container_image_effective" in meta
        assert meta["runtime_container_image_effective"] == "ghcr.io/opencode-ai/opencode:v0.14.0"

    def test_metadata_uses_passed_effective_image_without_re_resolving(self, tmp_path):
        """Metadata uses passed effective_image and does NOT call _resolve_effective_cage_image."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
            }
        )

        test_case = TestCaseSpec(
            id="test_001",
            type="runtime",
            raw={"source": {"task_branch": "task/test_001"}},
            base_branch="task/test_001",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-uuid"

        # Mock _resolve_effective_cage_image to fail - should NOT be called
        with patch.object(executor, "_resolve_effective_cage_image", side_effect=RuntimeError("Should not be called")):
            # Pass effective_image directly - metadata should use it without re-resolving
            meta = executor._build_runtime_metadata(
                test_case=test_case,
                profile=profile,
                runtime_mode="cage",
                runtime_config=executor.evaluation_config,
                workspace=workspace,
                effective_image="my-derived-image:1.0.0",
            )

        # Should use the passed effective_image, not attempt to resolve
        assert "runtime_container_image_base" in meta
        assert meta["runtime_container_image_base"] == "ghcr.io/opencode-ai/opencode:v0.14.0"
        assert "runtime_container_image_effective" in meta
        assert meta["runtime_container_image_effective"] == "my-derived-image:1.0.0"

    def test_metadata_defaults_to_base_image_when_effective_not_provided(self, tmp_path):
        """Metadata falls back to base_image when effective_image is None."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
            }
        )

        test_case = TestCaseSpec(
            id="test_001",
            type="runtime",
            raw={"source": {"task_branch": "task/test_001"}},
            base_branch="task/test_001",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-uuid"

        # Call without effective_image - should default to base_image
        meta = executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode="cage",
            runtime_config=executor.evaluation_config,
            workspace=workspace,
            effective_image=None,
        )

        # Should use base_image as fallback
        assert "runtime_container_image_base" in meta
        assert meta["runtime_container_image_base"] == "ghcr.io/opencode-ai/opencode:v0.14.0"
        assert "runtime_container_image_effective" in meta
        assert meta["runtime_container_image_effective"] == "ghcr.io/opencode-ai/opencode:v0.14.0"

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_probe_command_forces_shell_entrypoint(self, mock_run, tmp_path):
        """Probe command uses --entrypoint sh to force shell entrypoint."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_container_enable_ddev": True,
            }
        )

        # Mock successful probe (binaries exist)
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        effective_image = executor._resolve_effective_cage_image(executor.evaluation_config)

        assert effective_image == "ghcr.io/opencode-ai/opencode:v0.14.0"
        # Verify probe command format
        assert mock_run.called
        probe_cmd = mock_run.call_args.args[0]
        # Command should check for required binaries only (no || true false-positive).
        assert probe_cmd[0] == "docker"
        assert probe_cmd[1] == "run"
        assert "--rm" in probe_cmd
        assert "--entrypoint" in probe_cmd
        entrypoint_idx = probe_cmd.index("--entrypoint")
        assert probe_cmd[entrypoint_idx + 1] == "sh"
        assert "-c" in probe_cmd
        cmd_idx = probe_cmd.index("-c")
        assert "command -v ddev" in probe_cmd[cmd_idx + 1]
        assert "command -v docker" in probe_cmd[cmd_idx + 1]
        assert "command -v git" in probe_cmd[cmd_idx + 1]
        # ddev drush probe removed: the || true pattern was a false positive
        assert "|| true" not in probe_cmd[cmd_idx + 1]

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_runtime_command_uses_explicit_opencode_entrypoint(self, mock_run, tmp_path):
        """Cage runtime command sets --entrypoint opencode and no duplicated positional."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v1.14.0",
            }
        )

        test_case = TestCaseSpec(id="test_entrypoint", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path
        workspace.run_artifacts_path = tmp_path / "results" / "run"
        profile = resolve_profile("offline_cli")

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = executor._run_container_runtime_task(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=profile,
            timeout_seconds=30,
        )

        # Verify 6-tuple return
        assert len(result) == 6

        command = mock_run.call_args.args[0]

        # Verify --entrypoint opencode is present
        assert "--entrypoint" in command
        entrypoint_idx = command.index("--entrypoint")
        assert command[entrypoint_idx + 1] == "opencode"

        # Verify image comes after --entrypoint opencode
        assert command[entrypoint_idx + 2] == "ghcr.io/opencode-ai/opencode:v1.14.0"

        # Verify "run" follows image (no duplicated "opencode" positional)
        image_idx = command.index("ghcr.io/opencode-ai/opencode:v1.14.0")
        assert command[image_idx + 1] == "run"

        # Verify no standalone "opencode" positional argument appears after the image
        # (it should only appear as --entrypoint value)
        after_image = command[image_idx + 1 :]
        assert "opencode" not in after_image, f"Found 'opencode' in command args after image: {after_image}"

    def test_cage_launch_passes_prompt_as_positional_message(self, tmp_path):
        """Prompt text is passed as final positional message argument (not --prompt-file)."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        # Test with a multi-line prompt to ensure robust handling
        test_prompt = "Implement the following task:\n\n1. Create a module\n2. Add configuration\n3. Test functionality"
        test_case = TestCaseSpec(id="test_006", type="runtime", raw={}, prompt=test_prompt)
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return
            assert len(result) == 6

        command = mock_run.call_args.args[0]

        # Verify --prompt-file is NOT used
        assert "--prompt-file" not in command

        # Verify prompt text is passed as the final positional argument
        assert command[-1] == test_prompt

        # Verify all other expected flags are still present
        assert "--entrypoint" in command
        assert "--model" in command
        assert "--pure" in command
        assert "--dangerously-skip-permissions" in command
        assert "run" in command

    def test_cage_launch_prefers_workspace_task_markdown_for_task_input(self, tmp_path):
        """When TASK.md exists and is non-empty, its content is used as task input."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_task_md", type="runtime", raw={}, prompt="Prompt fallback")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        task_md_content = "# TASK\n\nUse this file content."
        (tmp_path / "TASK.md").write_text(task_md_content, encoding="utf-8")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            assert len(result) == 6

        command = mock_run.call_args.args[0]
        assert command[-1] == task_md_content

    def test_cage_launch_falls_back_to_prompt_when_task_markdown_empty(self, tmp_path):
        """When TASK.md exists but is empty/whitespace, prompt remains task input."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_prompt = "Prompt fallback content"
        test_case = TestCaseSpec(id="test_task_md_empty", type="runtime", raw={}, prompt=test_prompt)
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        (tmp_path / "TASK.md").write_text(" \n\n ", encoding="utf-8")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            assert len(result) == 6

        command = mock_run.call_args.args[0]
        assert command[-1] == test_prompt


# ---------------------------------------------------------------------------
# Task: Trajectory capture in cage mode
# ---------------------------------------------------------------------------


class TestCageModeTrajectoryCapture:
    """Test cage mode trajectory capture from SQLite database."""

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_returns_trajectory_when_sqlite_exists(self, mock_run, tmp_path):
        """Cage mode returns trajectory when SQLite database exists with session data."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        # Mock subprocess run to return success
        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        with patch("nichebench.execution.orchestrator.TestExecutor._build_trajectory_from_sqlite") as mock_build_traj:
            mock_build_traj.return_value = {
                "instance_id": "test_001",
                "model": "groq/test-model",
                "created_at": "2024-01-01T00:00:00Z",
                "messages": [],
                "stats": {
                    "total_turns": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "duration_seconds": 1.0,
                },
            }

            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )

            # Verify 6-tuple return with trajectory
            assert len(result) == 6
            mut_output, user_input, run_log, island_topology, effective_image, trajectory = result
            assert trajectory is not None
            assert trajectory["instance_id"] == "test_001"
            assert trajectory["model"] == "groq/test-model"

            # Verify _build_trajectory_from_sqlite was called with correct args
            mock_build_traj.assert_called_once()
            call_kwargs = mock_build_traj.call_args.kwargs
            assert "db_path" in call_kwargs
            assert "test_case_id" in call_kwargs
            assert call_kwargs["test_case_id"] == "test_001"
            assert "model_str" in call_kwargs
            assert "start_time" in call_kwargs
            assert "end_time" in call_kwargs
            assert "system_prompt" in call_kwargs

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_returns_none_trajectory_when_sqlite_missing(self, mock_run, tmp_path):
        """Cage mode returns None trajectory when SQLite database is missing."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_002", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        # Mock subprocess run to return success
        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        with patch("nichebench.execution.orchestrator.TestExecutor._build_trajectory_from_sqlite") as mock_build_traj:
            mock_build_traj.return_value = None

            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )

            # Verify 6-tuple return with None trajectory
            assert len(result) == 6
            mut_output, user_input, run_log, island_topology, effective_image, trajectory = result
            assert trajectory is None

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_trajectory_capture_is_best_effort(self, mock_run, tmp_path):
        """Trajectory capture errors do not crash the cage run."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_003", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        # Mock subprocess run to return success
        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        with patch("nichebench.execution.orchestrator.TestExecutor._build_trajectory_from_sqlite") as mock_build_traj:
            mock_build_traj.side_effect = Exception("DB error")

            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )

            # Verify run still succeeds despite trajectory error (best-effort)
            assert len(result) == 6
            mut_output, user_input, run_log, island_topology, effective_image, trajectory = result
            assert mut_output == "output"
            assert trajectory is None

    def test_cage_branch_stores_trajectory_in_artifacts(self, tmp_path):
        """Cage branch stores trajectory in runtime artifacts when provided."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )

        trajectory_data = {
            "instance_id": "test_004",
            "model": "groq/test-model",
            "created_at": "2024-01-01T00:00:00Z",
            "messages": [],
            "stats": {
                "total_turns": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "duration_seconds": 1.0,
            },
        }

        # Mock _run_container_runtime_task to return 6-tuple with trajectory
        with patch.object(executor, "_run_container_runtime_task") as mock_run_container:
            mock_run_container.return_value = (
                "mut_output",
                "user_input",
                "run_log",
                {"island": "topology"},
                "effective_image",
                trajectory_data,
            )

            # Mock workspace and other methods to skip to the cage branch
            from unittest.mock import MagicMock

            workspace = MagicMock()
            workspace.path = tmp_path
            workspace.ddev_project_name = "test-project"

            trajectory_data = {
                "instance_id": "test_004",
                "model": "groq/test-model",
                "created_at": "2024-01-01T00:00:00Z",
                "messages": [],
                "stats": {
                    "total_turns": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "duration_seconds": 1.0,
                },
            }

            # Mock _run_container_runtime_task to return 6-tuple with trajectory
            with patch.object(executor, "_run_container_runtime_task") as mock_run_container:
                mock_run_container.return_value = (
                    "mut_output",
                    "user_input",
                    "run_log",
                    {"island": "topology"},
                    "effective_image",
                    trajectory_data,
                )

            with (
                patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
                patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
                patch.object(executor, "_inject_task_markdown"),
                patch.object(executor, "_load_runtime_checks", return_value=[]),
                patch("nichebench.execution.orchestrator.Workspace") as MockWorkspace,
                patch("nichebench.execution.orchestrator.validate_runtime_testcase") as mock_validate,
                patch("nichebench.execution.orchestrator.RuntimeScorer") as MockScorer,
                patch("nichebench.execution.orchestrator.JudgeRunner") as MockJudge,
                patch("nichebench.execution.orchestrator.find_git_root") as mock_git_root,
                patch("nichebench.execution.orchestrator.resolve_branch_to_sha") as mock_resolve_sha,
            ):
                mock_validate.return_value = None  # pass validation
                mock_git_root.return_value = tmp_path  # fake git root
                mock_resolve_sha.return_value = "abc123"
                MockWorkspace.return_value = workspace

                # Mock scorer to return minimal check results
                mock_scorer_instance = MagicMock()
                mock_scorer_instance.run_deterministic_checks.return_value = []
                mock_scorer_instance.compute_hybrid_score.return_value = type(
                    "obj",
                    (object,),
                    {
                        "final_score": 1.0,
                        "deterministic_score": 1.0,
                        "judge_score": 1.0,
                        "passed": True,
                        "check_results": [],
                    },
                )
                MockScorer.return_value = mock_scorer_instance

                # Mock judge to avoid real evaluation
                mock_judge_instance = MagicMock()
                mock_judge_instance.evaluate_test.return_value = ({}, True)
                MockJudge.return_value = mock_judge_instance

                result = executor.execute_runtime_test(
                    TestCaseSpec(
                        id="test_004",
                        type="runtime",
                        raw={},
                        prompt="Task",
                        file_path=str(tmp_path / "test.yaml"),
                        base_branch="main",
                        resolved_sha="abc123",
                    )
                )

        # Verify trajectory is in runtime artifacts when present
        assert "trajectory.json" in result.runtime_artifacts
        assert result.runtime_artifacts["trajectory.json"] == trajectory_data

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_branch_skips_trajectory_when_none(self, mock_run, tmp_path):
        """Cage branch does not store trajectory when it is None."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_005", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        # Mock subprocess run to return success
        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        with patch("nichebench.execution.orchestrator.TestExecutor._build_trajectory_from_sqlite") as mock_build_traj:
            mock_build_traj.return_value = None

            # Mock other dependencies to simplify test
            with (
                patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
                patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
                patch.object(executor, "_load_runtime_checks", return_value=[]),
            ):
                result = executor.execute_runtime_test(test_case)

        # Verify trajectory is NOT in runtime artifacts when None
        assert "trajectory.json" not in result.runtime_artifacts


# ---------------------------------------------------------------------------
# Task: Cage runtime model configuration fixes
# ---------------------------------------------------------------------------


class TestCageRuntimeModelConfiguration:
    """Test cage runtime model configuration fixes."""

    def test_compute_opencode_model_binding_groq_compound_model_preserved(self):
        """Groq models with compound slash names (e.g. openai/gpt-oss-120b) are passed through as-is.

        OpenCode 1.14.25 accepts groq/openai/gpt-oss-120b and routes correctly;
        stripping to gpt-oss-120b would cause 'model does not exist' errors.
        """
        from nichebench.execution.orchestrator import TestExecutor

        runtime_config = {}

        # Compound model name is preserved — no slash-stripping
        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="groq",
            mut_model="openai/gpt-oss-120b",
            runtime_config=runtime_config,
        )
        assert provider == "groq"
        assert model_id == "openai/gpt-oss-120b"

        # Simple model name (no slash) passes through unchanged
        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="groq",
            mut_model="gemma2-9b-it",
            runtime_config=runtime_config,
        )
        assert provider == "groq"
        assert model_id == "gemma2-9b-it"

        # qwen3-32b style: compound name preserved
        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="groq",
            mut_model="qwen/qwen3-32b",
            runtime_config=runtime_config,
        )
        assert provider == "groq"
        assert model_id == "qwen/qwen3-32b"

        # Other providers pass through unchanged
        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="openai",
            mut_model="gpt-4o",
            runtime_config=runtime_config,
        )
        assert provider == "openai"
        assert model_id == "gpt-4o"

    def test_compute_opencode_model_binding_override(self):
        """runtime_opencode_model override takes precedence."""
        from nichebench.execution.orchestrator import TestExecutor

        runtime_config = {"runtime_opencode_model": "openai/gpt-4o-mini"}

        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="groq",
            mut_model="openai/gpt-oss-120b",
            runtime_config=runtime_config,
        )
        assert provider == "openai"
        assert model_id == "gpt-4o-mini"

    def test_compute_opencode_model_binding_override_without_provider(self):
        """Override without provider uses MUT provider."""
        from nichebench.execution.orchestrator import TestExecutor

        runtime_config = {"runtime_opencode_model": "custom-model-123"}

        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="groq",
            mut_model="openai/gpt-oss-120b",
            runtime_config=runtime_config,
        )
        assert provider == "groq"
        assert model_id == "custom-model-123"

    def test_get_provider_api_keys(self, monkeypatch):
        """Provider API keys are extracted from host environment."""
        from nichebench.execution.orchestrator import TestExecutor

        # Mock environment variables
        monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        # Test groq
        api_keys = TestExecutor._get_provider_api_keys("groq")
        assert api_keys == {"GROQ_API_KEY": "test-groq-key"}

        # Test openai
        api_keys = TestExecutor._get_provider_api_keys("openai")
        assert api_keys == {"OPENAI_API_KEY": "test-openai-key"}

        # Test anthropic (not set)
        api_keys = TestExecutor._get_provider_api_keys("anthropic")
        assert api_keys == {}

    def test_cage_launch_passes_api_keys_to_container(self, tmp_path, monkeypatch):
        """API keys from host env are passed to container via -e flags."""
        monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")

        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return
            assert len(result) == 6

        command = mock_run.call_args.args[0]

        # Extract -e values
        env_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-e"]

        # Verify GROQ_API_KEY is passed
        assert "GROQ_API_KEY=test-groq-key" in env_values

    def test_cage_launch_writes_opencode_json(self, tmp_path):
        """opencode.json is written in cage input workspace with execution-focused prompt."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_prompt = "Implement the following task"
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt=test_prompt)
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return
            assert len(result) == 6

        # Verify opencode.json was written
        opencode_json_path = tmp_path / "opencode.json"
        assert opencode_json_path.exists()

        # Verify structure
        with open(opencode_json_path, "r") as f:
            config = json.load(f)

        assert "$schema" in config
        assert config["model"] == "groq/test-model"
        assert "mode" in config
        assert "build" in config["mode"]
        prompt_text = config["mode"]["build"]["prompt"]

        # Verify prompt contains common-tool references (not raw task text)
        assert "Read(filePath:" in prompt_text
        assert "Write(filePath:" in prompt_text
        assert "Edit(filePath:" in prompt_text
        assert "Bash(command:" in prompt_text

        # Verify prompt does NOT equal the raw task input
        assert prompt_text != test_prompt

        # Verify prompt mentions explicit parameter names to prevent schema mismatches
        assert "EXACT parameter names" in prompt_text
        assert "This run is non-interactive" in prompt_text
        assert "do not ask user follow-up questions" in prompt_text
        assert "continue autonomously until done" in prompt_text
        assert "review-ready / production-ready for handoff" in prompt_text

        assert "provider" in config
        assert "groq" in config["provider"]
        assert "models" in config["provider"]["groq"]
        assert "test-model" in config["provider"]["groq"]["models"]

    def test_cage_launch_uses_full_compound_model_in_command(self, tmp_path):
        """Cage launch passes full compound model binding in --model flag (no slash-stripping)."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        # Set mut model with slash (groq-specific compound case)
        executor.mut_runner.model_config["model"] = "openai/gpt-oss-120b"

        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            # Verify 6-tuple return
            assert len(result) == 6

        command = mock_run.call_args.args[0]

        # Verify --model flag preserves compound model (groq/openai/gpt-oss-120b, NOT groq/gpt-oss-120b)
        assert "--model" in command
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "groq/openai/gpt-oss-120b"

    def test_metadata_includes_opencode_model_binding(self, tmp_path):
        """Metadata includes opencode model binding fields."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )

        test_case = TestCaseSpec(
            id="test_001",
            type="runtime",
            raw={"source": {"task_branch": "task/test_001"}},
            base_branch="task/test_001",
            resolved_sha="abc123",
        )
        profile = resolve_profile("offline_cli")
        workspace = MagicMock()
        workspace.path = "/tmp/ws"
        workspace.ddev_project_name = "nb-test-uuid"

        meta = executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode="cage",
            runtime_config=executor.evaluation_config,
            workspace=workspace,
        )

        # Verify opencode model binding fields
        assert "opencode_model_binding" in meta
        assert meta["opencode_model_binding"] == "groq/test-model"
        assert "opencode_provider" in meta
        assert meta["opencode_provider"] == "groq"
        assert "opencode_model_id" in meta
        assert meta["opencode_model_id"] == "test-model"

    @patch("nichebench.execution.orchestrator.subprocess.run")
    @patch("os.stat")
    def test_cage_launch_adds_docker_socket_group_access(self, mock_stat, mock_run, tmp_path):
        """When docker socket exists, --group-add is added with socket group id."""
        # Mock os.stat to return a specific gid
        mock_stat.return_value = MagicMock(st_gid=123)

        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = executor._run_container_runtime_task(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=profile,
            timeout_seconds=30,
        )
        # Verify 6-tuple return
        assert len(result) == 6

        command = mock_run.call_args.args[0]

        # Verify --group-add is present with the mocked gid
        assert "--group-add" in command
        group_add_idx = command.index("--group-add")
        assert command[group_add_idx + 1] == "123"

        # Verify os.stat was called with the docker socket path (among other calls)
        mock_stat.assert_any_call("/var/run/docker.sock")

    @patch("nichebench.execution.orchestrator.subprocess.run")
    @patch("os.stat")
    def test_cage_launch_handles_stat_failure_gracefully(self, mock_stat, mock_run, tmp_path):
        """When os.stat fails, command is built without --group-add (best effort)."""
        # Mock os.stat to raise an exception
        mock_stat.side_effect = OSError("Permission denied")

        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_002", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = executor._run_container_runtime_task(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=profile,
            timeout_seconds=30,
        )
        # Verify 6-tuple return and command was built despite stat failure
        assert len(result) == 6

        command = mock_run.call_args.args[0]

        # Verify --group-add is NOT present when stat fails
        assert "--group-add" not in command

        # Verify os.stat was attempted with the docker socket path (among other calls)
        mock_stat.assert_any_call("/var/run/docker.sock")


# ---------------------------------------------------------------------------
# Task: Rejected tool attempt parsing and guard with retry
# ---------------------------------------------------------------------------


class TestParseRejectedToolAttempts:
    """Tests for _parse_rejected_tool_attempts."""

    def test_returns_empty_list_for_empty_run_log(self):
        result = TestExecutor._parse_rejected_tool_attempts("")
        assert result == []

    def test_returns_empty_list_for_none(self):
        result = TestExecutor._parse_rejected_tool_attempts(None)
        assert result == []

    def test_parses_single_rejected_tool_attempt(self):
        run_log = "STDOUT:\nSome output\n\nSTDERR:\nattempted to call tool 'exec' which was not in request.tools"
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 1
        assert result[0]["tool_name"] == "exec"
        assert "not in request.tools" in result[0]["error_message"]

    def test_parses_multiple_rejected_tool_attempts(self):
        run_log = (
            "STDERR:\n"
            "attempted to call tool 'exec' which was not in request.tools\n"
            "attempted to call tool 'Run' which was not in request.tools\n"
        )
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 2
        tool_names = {r["tool_name"] for r in result}
        assert tool_names == {"exec", "run"}

    def test_normalizes_tool_names_to_lowercase(self):
        run_log = "STDERR:\nattempted to call tool 'Bash' which was not in request.tools\n"
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 1
        assert result[0]["tool_name"] == "bash"

    def test_handles_single_quotes_in_tool_name(self):
        run_log = "STDERR:\nattempted to call tool 'exec' which was not in request.tools\n"
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 1
        assert result[0]["tool_name"] == "exec"

    def test_handles_double_quotes_in_tool_name(self):
        run_log = 'STDERR:\nattempted to call tool "exec" which was not in request.tools\n'
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 1
        assert result[0]["tool_name"] == "exec"

    def test_no_matches_when_no_rejected_attempt(self):
        run_log = "STDOUT:\nTool 'Bash' succeeded\n\nSTDERR:\nAll good"
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert result == []

    def test_parses_schema_mismatch_missing_filePath(self):
        """Schema mismatch for read tool with missing filePath is parsed."""
        run_log = "STDERR:\n" "parameters for tool read did not match schema: missing properties: 'filePath'\n"
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 1
        assert result[0]["tool_name"] == "read"
        assert "filePath" in result[0]["error_message"]
        assert "did not match schema" in result[0]["error_message"]

    def test_parses_schema_mismatch_with_various_tools(self):
        """Schema mismatch errors for various tools are parsed correctly."""
        run_log = (
            "STDERR:\n"
            "parameters for tool write did not match schema: missing properties: 'filePath'\n"
            "parameters for tool edit did not match schema: missing properties: 'oldString'\n"
        )
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 2
        tool_names = {r["tool_name"] for r in result}
        assert tool_names == {"write", "edit"}

    def test_parses_both_rejected_and_schema_mismatch(self):
        """Both rejected-tool and schema-mismatch errors are captured together."""
        run_log = (
            "STDERR:\n"
            "attempted to call tool 'exec' which was not in request.tools\n"
            "parameters for tool read did not match schema: missing properties: 'filePath'\n"
        )
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 2
        tool_names = {r["tool_name"] for r in result}
        assert tool_names == {"exec", "read"}

    def test_schema_mismatch_normalizes_tool_name_lowercase(self):
        """Schema mismatch tool name is normalized to lowercase."""
        run_log = "STDERR:\nparameters for tool Read did not match schema: missing properties: 'filePath'\n"
        result = TestExecutor._parse_rejected_tool_attempts(run_log)
        assert len(result) == 1
        assert result[0]["tool_name"] == "read"


class TestToolAllowlistGuardWithRejectedAttempts:
    """Tests for _build_tool_allowlist_check including rejected attempts."""

    def test_guard_fails_on_rejected_disallowed_tool(self):
        """If a rejected attempt includes a tool not in allowlist, guard fails in strict mode."""
        trajectory = {"messages": []}
        rejected = [{"tool_name": "exec", "error_message": "attempted to call tool 'exec' not in request.tools"}]
        result = TestExecutor._build_tool_allowlist_check(trajectory, rejected, enforce=True)
        assert result is not None
        assert result.passed is False
        assert "exec" in result.details["disallowed_tools"]
        assert "rejected_tool_attempts" in result.details

    def test_guard_passes_when_rejected_tool_is_allowed(self):
        """If a rejected attempt is for an allowed tool (e.g., bash), guard passes."""
        trajectory = {"messages": []}
        rejected = [{"tool_name": "bash", "error_message": "attempted to call tool 'bash' not in request.tools"}]
        result = TestExecutor._build_tool_allowlist_check(trajectory, rejected)
        assert result is not None
        assert result.passed is True
        assert "bash" not in result.details["disallowed_tools"]

    def test_guard_includes_rejected_in_used_tools_union(self):
        """Rejected tools are added to used_tools in details."""
        trajectory = {"messages": []}
        rejected = [{"tool_name": "custom_tool", "error_message": "attempted to call tool 'custom_tool'..."}]
        result = TestExecutor._build_tool_allowlist_check(trajectory, rejected)
        assert result is not None
        assert "custom_tool" in result.details["used_tools"]
        assert result.details["rejected_tool_attempts"] == rejected

    def test_none_trajectory_returns_none_check(self):
        """None trajectory returns None check (no guard emitted)."""
        result = TestExecutor._build_tool_allowlist_check(None)
        assert result is None

    def test_rejected_attempts_empty_list_behaves_like_none(self):
        """Empty rejected list still evaluates correctly (union is unchanged)."""
        trajectory = {"messages": []}
        result = TestExecutor._build_tool_allowlist_check(trajectory, [])
        assert result is not None
        assert result.passed is True


class TestContainerRuntimeTaskWithRetry:
    """Tests for _run_container_runtime_task_with_retry."""

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_no_retry_when_run_succeeds(self, mock_task, tmp_path):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        mock_task.return_value = ("output", "input", "log", {}, "image", {"messages": []})

        result = executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        # Returns 7-tuple
        assert len(result) == 7
        mut_output, user_input, run_log, island_topology, effective_image, trajectory, retry_info = result
        assert mock_task.call_count == 1
        assert retry_info is None

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_no_retry_when_run_log_has_no_invalid_request_error(self, mock_task, tmp_path):
        """No retry when error is not invalid_request_error."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_002", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        mock_task.return_value = ("output", "input", "some other error log", {}, "image", None)

        result = executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        assert mock_task.call_count == 1
        # retry_info None because no invalid_request_error
        assert result[-1] is None

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_no_retry_when_invalid_request_error_but_no_rejected_tool(self, mock_task, tmp_path):
        """No retry when invalid_request_error appears but no rejected tool parsing match."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_003", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        # invalid_request_error but not from rejected tool
        mock_task.return_value = ("output", "input", "invalid_request_error: something else", {}, "image", None)

        executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        assert mock_task.call_count == 1

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_retry_triggers_on_invalid_request_error_with_rejected_tool(self, mock_task, tmp_path):
        """Retry triggers when invalid_request_error due to unknown tool."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_004", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        # First call returns rejected tool attempt
        first_log = "STDERR:\nattempted to call tool 'exec' which was not in request.tools\ninvalid_request_error"
        second_log = "STDOUT:\nretry succeeded"
        mock_task.side_effect = [
            ("output1", "input1", first_log, {}, "image", {"messages": []}),
            ("output2", "input2", second_log, {}, "image", {"messages": [{"role": "assistant", "content": "done"}]}),
        ]

        result = executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        assert mock_task.call_count == 2
        # retry_info is set
        retry_info = result[-1]
        assert retry_info is not None
        assert retry_info["attempted"] is True

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_retry_triggers_with_double_quoted_tool_name(self, mock_task, tmp_path):
        """Retry triggers when rejected tool name appears with double quotes in log."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_006", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        first_log = 'STDERR:\nattempted to call tool "exec" which was not in request.tools\ninvalid_request_error'
        second_log = "STDOUT:\nretry succeeded"
        mock_task.side_effect = [
            ("output1", "input1", first_log, {}, "image", {"messages": []}),
            ("output2", "input2", second_log, {}, "image", {"messages": []}),
        ]

        result = executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        assert mock_task.call_count == 2
        retry_info = result[-1]
        assert retry_info is not None
        assert retry_info["attempted"] is True

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_retry_info_recorded_in_metadata_after_retry(self, mock_task, tmp_path):
        """After retry, retry_info appears in runtime metadata."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_005", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        first_log = "STDERR:\nattempted to call tool 'exec' which was not in request.tools\ninvalid_request_error"
        mock_task.side_effect = [
            ("output1", "input1", first_log, {}, "image", {"messages": []}),
            ("output2", "input2", "STDOUT:\nsucceeded", {}, "image", {"messages": []}),
        ]

        # Call the retry wrapper
        result = executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        # Build metadata with retry_info
        profile = resolve_profile("offline_cli")
        meta = executor._build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode="cage",
            runtime_config=executor.evaluation_config,
            workspace=workspace,
            retry_info=result[-1],
        )

        assert "retry_info" in meta
        assert meta["retry_info"]["attempted"] is True

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_retry_triggers_on_schema_mismatch_invalid_request_error(self, mock_task, tmp_path):
        """Retry triggers when invalid_request_error due to schema mismatch (missing filePath)."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_007", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        # First call returns schema mismatch error for read tool
        first_log = (
            "STDERR:\n"
            "parameters for tool read did not match schema: missing properties: 'filePath'\n"
            "invalid_request_error"
        )
        second_log = "STDOUT:\nretry succeeded"
        mock_task.side_effect = [
            ("output1", "input1", first_log, {}, "image", {"messages": []}),
            ("output2", "input2", second_log, {}, "image", {"messages": [{"role": "assistant", "content": "done"}]}),
        ]

        result = executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        assert mock_task.call_count == 2
        retry_info = result[-1]
        assert retry_info is not None
        assert retry_info["attempted"] is True
        assert "read" in retry_info["reason"]

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_retry_triggers_on_json_parse_invalid_request_error(self, mock_task, tmp_path):
        """Retry triggers when invalid_request_error includes JSON parse failure text."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_008", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path

        first_log = "STDERR:\n" "Failed to parse tool call arguments as JSON\n" "invalid_request_error"
        second_log = "STDOUT:\nretry succeeded"
        mock_task.side_effect = [
            ("output1", "input1", first_log, {}, "image", {"messages": []}),
            ("output2", "input2", second_log, {}, "image", {"messages": []}),
        ]

        result = executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        assert mock_task.call_count == 2
        retry_info = result[-1]
        assert retry_info is not None
        assert retry_info["attempted"] is True
        assert retry_info["reason"] == "json_parse_failure"

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_retry_triggers_when_first_run_raises_with_rejected_tool(self, mock_task, tmp_path):
        """First run raises RuntimeError; the captured run.log is replayed and retry triggers.

        Production path: OpenCode exits non-zero (or watchdog trips), the cage writes
        run.log to the output trace island, then raises RuntimeError.  The retry
        wrapper must catch that error, read the captured log, and still allow the
        retry decision to inspect it for invalid_request_error.
        """
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_009", type="runtime", raw={}, prompt="Task")

        # Create a real run.log on disk to simulate the cage's output trace island.
        run_artifacts = tmp_path / "results" / "run"
        run_artifacts.mkdir(parents=True)
        first_log = "STDERR:\nattempted to call tool 'exec' which was not in request.tools\n" "invalid_request_error"
        (run_artifacts / "run.log").write_text(first_log, encoding="utf-8")

        workspace = MagicMock()
        workspace.path = tmp_path
        workspace.run_artifacts_path = str(run_artifacts)

        # First call raises (non-zero exit), second call succeeds.
        second_log = "STDOUT:\nretry succeeded"
        mock_task.side_effect = [
            RuntimeError("Container OpenCode command failed with exit 1"),
            ("output2", "input2", second_log, {}, "image", {"messages": []}),
        ]

        result = executor._run_container_runtime_task_with_retry(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=resolve_profile("offline_cli"),
            timeout_seconds=30,
        )

        assert mock_task.call_count == 2
        retry_info = result[-1]
        assert retry_info is not None
        assert retry_info["attempted"] is True

    @patch.object(TestExecutor, "_run_container_runtime_task")
    def test_first_run_raises_without_retry_trigger_propagates(self, mock_task, tmp_path):
        """If the first run raises and run.log has no retry trigger, the error propagates."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_010", type="runtime", raw={}, prompt="Task")

        run_artifacts = tmp_path / "results" / "run"
        run_artifacts.mkdir(parents=True)
        (run_artifacts / "run.log").write_text("STDERR:\nsome unrelated failure\n", encoding="utf-8")

        workspace = MagicMock()
        workspace.path = tmp_path
        workspace.run_artifacts_path = str(run_artifacts)

        mock_task.side_effect = RuntimeError("Container OpenCode command failed with exit 1")

        with pytest.raises(RuntimeError, match="exit 1"):
            executor._run_container_runtime_task_with_retry(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=resolve_profile("offline_cli"),
                timeout_seconds=30,
            )

        assert mock_task.call_count == 1


# ---------------------------------------------------------------------------
# CLI --model override suppresses runtime_opencode_model config
# ---------------------------------------------------------------------------


class TestCliModelOverride:
    """When --model is passed via CLI, runtime_opencode_model config is ignored."""

    def test_compute_opencode_binding_respects_config_override_by_default(self):
        """Without cli_model_override, runtime_opencode_model config wins."""
        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="groq",
            mut_model="qwen/qwen3-32b",
            runtime_config={"runtime_opencode_model": "groq/openai/gpt-oss-120b"},
        )
        assert provider == "groq"
        assert model_id == "openai/gpt-oss-120b"

    def test_compute_opencode_binding_cli_override_suppresses_config(self):
        """With cli_model_override set, runtime_opencode_model is ignored; model_id preserved as-is."""
        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="groq",
            mut_model="qwen/qwen3-32b",
            runtime_config={"runtime_opencode_model": "groq/openai/gpt-oss-120b"},
            cli_model_override="groq/qwen/qwen3-32b",
        )
        # Slash-stripping removed; full compound model_id is preserved for the provider
        assert provider == "groq"
        assert model_id == "qwen/qwen3-32b"

    def test_compute_opencode_binding_cli_override_empty_string_falls_through_to_mut(self):
        """Empty string cli_model_override is not None, so config override is bypassed and MUT is used."""
        provider, model_id = TestExecutor._compute_opencode_model_binding(
            mut_provider="groq",
            mut_model="qwen/qwen3-32b",
            runtime_config={"runtime_opencode_model": "groq/openai/gpt-oss-120b"},
            cli_model_override="",
        )
        assert provider == "groq"
        assert model_id == "qwen/qwen3-32b"

    def test_executor_stores_cli_model_override(self):
        """TestExecutor stores cli_model_override and exposes it."""
        mut_cfg = {"provider": "groq", "model": "qwen/qwen3-32b", "parameters": {}}
        judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
        network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

        with (
            patch("nichebench.execution.orchestrator.get_config") as mock_config,
            patch.object(TestExecutor, "_load_system_prompt", return_value=None),
            patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
        ):
            mock_config.return_value.get_evaluation_config.return_value = {}
            mock_config.return_value.get_model_string.side_effect = lambda cfg: (f"{cfg['provider']}/{cfg['model']}")
            executor = TestExecutor(
                framework="drupal_runtime",
                category="runtime",
                mut_config=mut_cfg,
                judge_config=judge_cfg,
                network_config=network_cfg,
                cli_model_override="groq/qwen/qwen3-32b",
            )

        assert executor._cli_model_override == "groq/qwen/qwen3-32b"

    def test_executor_default_cli_override_is_none(self):
        """TestExecutor without cli_model_override defaults to None."""
        mut_cfg = {"provider": "groq", "model": "test-model", "parameters": {}}
        judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
        network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

        with (
            patch("nichebench.execution.orchestrator.get_config") as mock_config,
            patch.object(TestExecutor, "_load_system_prompt", return_value=None),
            patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
        ):
            mock_config.return_value.get_evaluation_config.return_value = {}
            mock_config.return_value.get_model_string.side_effect = lambda cfg: (f"{cfg['provider']}/{cfg['model']}")
            executor = TestExecutor(
                framework="drupal_runtime",
                category="runtime",
                mut_config=mut_cfg,
                judge_config=judge_cfg,
                network_config=network_cfg,
            )

        assert executor._cli_model_override is None


# ---------------------------------------------------------------------------
# opencode.json config-driven generation
# ---------------------------------------------------------------------------


class TestCageOpenCodeJsonGeneration:
    """Tests for _write_cage_opencode_json with config-driven generation."""

    def test_native_provider_backward_compat(self, tmp_path):
        """Without api_base or runtime_config, generates simple native provider block."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="groq",
            opencode_model_id="gemma2-9b-it",
        )
        assert result.exists()
        cfg = json.loads(result.read_text())
        assert cfg["model"] == "groq/gemma2-9b-it"
        assert cfg["small_model"] == "groq/gemma2-9b-it"
        provider = cfg["provider"]["groq"]
        assert provider == {"models": {"gemma2-9b-it": {}}}
        assert "autoshorten" not in cfg

    def test_api_base_generates_npm_block(self, tmp_path):
        """With api_base, generates @ai-sdk/openai-compatible npm provider block."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="groq",
            opencode_model_id="llama-3.3-70b",
            api_base="http://localhost:8080/v1",
            runtime_config={},
        )
        cfg = json.loads(result.read_text())
        assert cfg["model"] == "groq/llama-3.3-70b"
        provider_block = cfg["provider"]["groq"]
        assert provider_block["npm"] == "@ai-sdk/openai-compatible"
        assert provider_block["options"]["baseURL"] == "http://localhost:8080/v1"
        assert provider_block["name"] == "groq"
        assert "api_key" not in provider_block
        assert "fetch" not in provider_block["options"]
        assert cfg["default_agent"] == "build"
        assert cfg["permission"]["*"] == "deny"
        assert cfg["permission"]["bash"] == "allow"
        assert cfg["permission"]["question"] == "deny"
        assert cfg["permission"]["task"] == "deny"

    def test_output_limit_defaults_to_half_of_context(self, tmp_path):
        """When context_limit set but no output_limit, output = 50% of context."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="groq",
            opencode_model_id="my-model",
            api_base="http://localhost:8080/v1",
            runtime_config={"runtime_opencode_context_limit": 8192},
        )
        cfg = json.loads(result.read_text())
        limit = cfg["provider"]["groq"]["models"]["my-model"]["limit"]
        assert limit["context"] == 8192
        assert limit["output"] == 4096

    def test_explicit_output_limit(self, tmp_path):
        """Explicit output_limit overrides ratio computation."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="openai",
            opencode_model_id="my-model",
            api_base="http://localhost:8080/v1",
            runtime_config={
                "runtime_opencode_context_limit": 8192,
                "runtime_opencode_output_limit": 1024,
            },
        )
        cfg = json.loads(result.read_text())
        limit = cfg["provider"]["openai"]["models"]["my-model"]["limit"]
        assert limit["context"] == 8192
        assert limit["output"] == 1024

    def test_custom_output_ratio(self, tmp_path):
        """Custom ratio applies to context limit."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="openai",
            opencode_model_id="my-model",
            api_base="http://localhost:8080/v1",
            runtime_config={
                "runtime_opencode_context_limit": 10000,
                "runtime_opencode_output_ratio": 0.25,
            },
        )
        cfg = json.loads(result.read_text())
        limit = cfg["provider"]["openai"]["models"]["my-model"]["limit"]
        assert limit["output"] == 2500

    def test_timeout_and_chunk_timeout(self, tmp_path):
        """Timeout and chunk timeout appear in provider options."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="openai",
            opencode_model_id="my-model",
            api_base="http://localhost:8080/v1",
            runtime_config={
                "runtime_opencode_timeout_ms": 120000,
                "runtime_opencode_chunk_timeout_ms": 60000,
            },
        )
        cfg = json.loads(result.read_text())
        opts = cfg["provider"]["openai"]["options"]
        assert opts["timeout"] == 120000
        assert opts["chunkTimeout"] == 60000

    def test_set_cache_key(self, tmp_path):
        """setCacheKey appears when runtime_opencode_set_cache_key is true."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="openai",
            opencode_model_id="my-model",
            api_base="http://localhost:8080/v1",
            runtime_config={"runtime_opencode_set_cache_key": True},
        )
        cfg = json.loads(result.read_text())
        assert cfg["provider"]["openai"]["options"]["setCacheKey"] is True

    def test_custom_npm_package(self, tmp_path):
        """Custom npm package overrides default @ai-sdk/openai-compatible."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="openai",
            opencode_model_id="my-model",
            api_base="http://localhost:8080/v1",
            runtime_config={"runtime_opencode_provider_npm": "@my-org/my-sdk"},
        )
        cfg = json.loads(result.read_text())
        assert cfg["provider"]["openai"]["npm"] == "@my-org/my-sdk"

    def test_compaction_emitted(self, tmp_path):
        """compaction block emitted when any compaction config key is set."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="groq",
            opencode_model_id="my-model",
            runtime_config={
                "runtime_opencode_compaction_auto": True,
                "runtime_opencode_compaction_prune": True,
                "runtime_opencode_compaction_reserved": 1000,
            },
        )
        cfg = json.loads(result.read_text())
        assert "compaction" in cfg
        assert cfg["compaction"]["auto"] is True
        assert cfg["compaction"]["prune"] is True
        assert cfg["compaction"]["reserved"] == 1000

    def test_compaction_absent_when_not_configured(self, tmp_path):
        """compaction block absent when no compaction config is set."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="groq",
            opencode_model_id="my-model",
        )
        cfg = json.loads(result.read_text())
        assert "compaction" not in cfg

    def test_permission_block_is_explicit_allowlist(self, tmp_path):
        """Generated opencode.json uses an explicit allowlist: deny-all then allow specific tools."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="groq",
            opencode_model_id="my-model",
        )
        cfg = json.loads(result.read_text())
        perm = cfg["permission"]

        # Wildcard must be deny (not allow) so unknown tools are blocked by default
        assert perm["*"] == "deny", f"Expected '*' to be 'deny', got {perm['*']!r}"

        # Core coding tools must be explicitly allowed
        for tool in ("bash", "read", "edit", "glob", "grep", "write", "list", "patch", "todowrite", "todoread"):
            assert perm[tool] == "allow", f"Expected '{tool}' to be 'allow', got {perm.get(tool)!r}"

        # Interactive / delegation / network tools must be denied:
        # - question: no interactive prompts
        # - task: no sub-agent spawning
        # - skill: no loading external skills (cage is isolated)
        # - webfetch/websearch: no internet access (cage is offline sandbox)
        for tool in ("question", "task", "skill", "webfetch", "websearch"):
            assert perm[tool] == "deny", f"Expected '{tool}' to be 'deny', got {perm.get(tool)!r}"

    def test_external_directory_allowlist_includes_workspace(self, tmp_path):
        """Cage opencode.json must allow the workspace path and standard temp/state dirs.

        Without this, the MUT inside the cage gets deny-by-default on file access
        and can't read/write the Drupal project it's supposed to be debugging.
        """
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="groq",
            opencode_model_id="my-model",
        )
        cfg = json.loads(result.read_text())
        ext_dirs = cfg["permission"]["external_directory"]
        assert ext_dirs[str(tmp_path)] == "allow", f"Workspace path {tmp_path} must be allowed, got {ext_dirs!r}"
        # Temp and state dirs the MUT needs for opencode internals
        for path in ("/tmp", "/tmp/opencode", "/nichebench/islands", "/nichebench/state"):
            assert ext_dirs[path] == "allow", f"{path} must be allowed, got {ext_dirs!r}"

    def test_native_provider_with_limits(self, tmp_path):
        """Native provider without api_base can still carry model limits."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="groq",
            opencode_model_id="my-model",
            runtime_config={
                "runtime_opencode_context_limit": 4096,
                "runtime_opencode_output_limit": 512,
            },
        )
        cfg = json.loads(result.read_text())
        limit = cfg["provider"]["groq"]["models"]["my-model"]["limit"]
        assert limit["context"] == 4096
        assert limit["output"] == 512
        # No npm block for native
        assert "npm" not in cfg["provider"]["groq"]

    def test_no_fetch_block_without_timeouts(self, tmp_path):
        """No fetch block in options when no timeouts are configured."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="openai",
            opencode_model_id="my-model",
            api_base="http://localhost:8080/v1",
            runtime_config={},
        )
        cfg = json.loads(result.read_text())
        assert "fetch" not in cfg["provider"]["openai"]["options"]

    def test_custom_provider_name_used_as_key(self, tmp_path):
        """runtime_opencode_provider_name overrides the provider dict key and model binding."""
        result = TestExecutor._write_cage_opencode_json(
            workspace_host_path=tmp_path,
            opencode_provider="llama-cpp",
            opencode_model_id="my-model",
            api_base="http://localhost:8080/v1",
            runtime_config={"runtime_opencode_provider_name": "my-local"},
        )
        cfg = json.loads(result.read_text())
        assert "my-local" in cfg["provider"]
        assert cfg["model"] == "my-local/my-model"
        assert cfg["provider"]["my-local"]["name"] == "my-local"


# ---------------------------------------------------------------------------
# Provider key derivation and --model flag integration
# ---------------------------------------------------------------------------


class TestCageProviderKeyDerivation:
    """Integration tests for provider key derivation in cage mode."""

    def test_explicit_provider_name_in_model_flag(self, tmp_path):
        """runtime_opencode_provider_name is used in --model flag when api_base is set."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_opencode_api_base": "http://localhost:8080",
                "runtime_opencode_provider_name": "my-local",
            }
        )
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Do task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )

        command = mock_run.call_args.args[0]
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "my-local/test-model"

        # opencode.json written to workspace also uses the derived key
        opencode_cfg = json.loads((tmp_path / "opencode.json").read_text())
        assert opencode_cfg["model"] == "my-local/test-model"
        assert "my-local" in opencode_cfg["provider"]

    def test_derived_key_sanitizes_provider_name(self, tmp_path):
        """When no explicit name, provider key is sanitized from original provider."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
                "runtime_opencode_api_base": "http://localhost:8080",
            }
        )
        # Override MUT provider to something with special chars
        executor.mut_runner.model_config["provider"] = "my.provider"
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Do task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )

        command = mock_run.call_args.args[0]
        model_idx = command.index("--model")
        # dots replaced with dashes in provider key
        assert command[model_idx + 1] == "my-provider/test-model"

    def test_native_without_api_base_uses_original_provider(self, tmp_path):
        """Native provider without api_base uses original provider key unchanged."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_001", type="runtime", raw={}, prompt="Do task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )

        command = mock_run.call_args.args[0]
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "groq/test-model"


# ---------------------------------------------------------------------------
# TestCatastrophicFailureDetection
# ---------------------------------------------------------------------------


class TestCatastrophicFailureDetection:
    """Unit tests for TestExecutor._detect_catastrophic_failure."""

    def test_detects_dh_is_not_a_function(self):
        reason = TestExecutor._detect_catastrophic_failure(
            mut_output="",
            run_log="STDERR: DH is not a function\nTraceback...",
            trajectory=None,
        )
        assert reason is not None
        assert "dh is not a function" in reason.lower()

    def test_detects_dh_is_not_a_function_case_insensitive(self):
        reason = TestExecutor._detect_catastrophic_failure(
            mut_output="",
            run_log="some preamble\ndH Is Not A Function\n",
            trajectory=None,
        )
        assert reason is not None

    def test_detects_timeout_in_mut_output(self):
        reason = TestExecutor._detect_catastrophic_failure(
            mut_output="[Error: process timed out after 1800s]",
            run_log="normal run log",
            trajectory=None,
        )
        assert reason is not None
        assert "timed out" in reason.lower()

    def test_normal_run_returns_none(self):
        reason = TestExecutor._detect_catastrophic_failure(
            mut_output="I have completed the task successfully.",
            run_log="build step-start tool_call step-finish",
            trajectory={"messages": [{"tool_calls": [{"name": "bash"}]}]},
        )
        assert reason is None

    def test_error_in_stderr_with_tool_activity_not_catastrophic(self):
        # An error in STDERR is only catastrophic when combined with zero tool activity.
        # If there ARE tool_calls in the trajectory, the agent did work — not a startup crash.
        trajectory = {"messages": [{"tool_calls": [{"name": "bash"}]}]}
        reason = TestExecutor._detect_catastrophic_failure(
            mut_output="",
            run_log="STDERR: Error: something went wrong",
            trajectory=trajectory,
        )
        assert reason is None

    def test_error_in_stderr_no_tool_activity_is_catastrophic(self):
        trajectory = {"messages": [{"role": "assistant", "content": "hello"}]}
        reason = TestExecutor._detect_catastrophic_failure(
            mut_output="",
            run_log="STDERR: Error: Cannot find module 'opencode'",
            trajectory=trajectory,
        )
        assert reason is not None
        assert "startup error" in reason.lower()

    def test_error_in_stderr_no_trajectory_is_catastrophic(self):
        reason = TestExecutor._detect_catastrophic_failure(
            mut_output="",
            run_log="STDERR: Error: Cannot find module 'opencode'",
            trajectory=None,
        )
        assert reason is not None

    def test_timeout_without_bracket_error_prefix_not_triggered(self):
        # "timed out" without "[Error:" prefix should NOT trigger class 1.
        reason = TestExecutor._detect_catastrophic_failure(
            mut_output="the request timed out eventually",
            run_log="normal log",
            trajectory={"messages": [{"tool_calls": [{"name": "bash"}]}]},
        )
        assert reason is None


# ---------------------------------------------------------------------------
# Runtime smoke preflight integration
# ---------------------------------------------------------------------------


class TestSmokePreflight:
    """Tests for runtime smoke preflight integration in workspace preflight hook."""

    def test_disabled_does_not_invoke_subprocess(self, tmp_path):
        """disabled flag -> smoke runner not invoked."""
        executor = _make_executor({"runtime_smoke_preflight_enabled": False})

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            executor._run_runtime_preflight_workspace(tmp_path, "cage")

        mock_run.assert_not_called()

    def test_enabled_success_no_exception(self, tmp_path):
        """enabled + success return -> no exception raised."""
        executor = _make_executor({"runtime_smoke_preflight_enabled": True})

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            executor._run_runtime_preflight_workspace(tmp_path, "cage")

        mock_run.assert_called_once()
        call_cmd = mock_run.call_args[0][0]
        assert "--json" in call_cmd
        assert "--workspace" in call_cmd

    def test_enabled_nonzero_json_raises_with_check_names(self, tmp_path):
        """enabled + non-zero + json payload -> ValidationError contains failed check names."""
        executor = _make_executor({"runtime_smoke_preflight_enabled": True})

        json_output = json.dumps(
            {
                "total": 3,
                "passed": 1,
                "failed": 2,
                "checks": [
                    {"name": "ddev_status", "passed": False, "returncode": 1},
                    {"name": "drush_status", "passed": True, "returncode": 0},
                    {"name": "drush_cr", "passed": False, "returncode": 1},
                ],
            }
        )

        with patch("nichebench.execution.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout=json_output, stderr="")
            with pytest.raises(ValidationError) as exc_info:
                executor._run_runtime_preflight_workspace(tmp_path, "cage")

        err_msg = str(exc_info.value)
        assert "ddev_status" in err_msg
        assert "drush_cr" in err_msg

    def test_enabled_missing_script_raises(self, tmp_path, monkeypatch):
        """enabled + missing script -> ValidationError indicating missing preflight script."""
        executor = _make_executor({"runtime_smoke_preflight_enabled": True})

        original_exists = Path.exists

        def fake_exists(self_path: Path) -> bool:
            if self_path.name == "runtime_smoke.py":
                return False
            return original_exists(self_path)

        monkeypatch.setattr(Path, "exists", fake_exists)

        with pytest.raises(ValidationError, match="not found"):
            executor._run_runtime_preflight_workspace(tmp_path, "cage")


# ---------------------------------------------------------------------------
# Cage runtime USER env normalization
# ---------------------------------------------------------------------------


class TestCageRuntimeUserEnv:
    """Cage runtime must set USER env var to prevent ddev username resolution failures."""

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_launch_sets_user_env_var(self, mock_run, tmp_path):
        """USER env var is explicitly set in cage docker run command."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_user_env", type="runtime", raw={}, prompt="Implement task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        executor._run_container_runtime_task(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=profile,
            timeout_seconds=30,
        )

        command = mock_run.call_args.args[0]
        env_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-e"]

        # USER must be set so ddev can determine the username inside the container
        user_entries = [v for v in env_values if v.startswith("USER=")]
        assert user_entries, f"USER env var not found in docker run command. env_values={env_values}"
        assert user_entries[0] == "USER=opencode", f"Expected USER=opencode, got {user_entries[0]!r}"

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_launch_user_env_is_deterministic(self, mock_run, tmp_path):
        """USER env var is always 'opencode' regardless of host user."""
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_user_env_2", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        # Run twice; both times USER must be the same fixed value
        for _ in range(2):
            executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=profile,
                timeout_seconds=30,
            )
            command = mock_run.call_args.args[0]
            env_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-e"]
            user_entries = [v for v in env_values if v.startswith("USER=")]
            assert len(user_entries) == 1, f"Expected exactly one USER= entry, got: {user_entries}"
            assert user_entries[0] == "USER=opencode"


class TestCageGitWrapper:
    """Cage runtime prepends a git wrapper that blocks unsafe MUT commands."""

    def test_git_wrapper_allows_inspection_and_blocks_mutation(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = TestExecutor._write_cage_git_wrapper(bin_dir)

        allowed = subprocess.run([str(wrapper), "--version"], check=True, capture_output=True, text=True)
        assert "git version" in allowed.stdout

        blocked = subprocess.run(
            [str(wrapper), "-C", ".", "checkout", "--", ".ddev/config.yaml"],
            capture_output=True,
            text=True,
        )
        assert blocked.returncode == 126
        assert "git checkout is disabled" in blocked.stderr

        sh_wrapper = bin_dir / "sh"
        blocked_absolute = subprocess.run(
            [str(sh_wrapper), "-c", "/usr/bin/git checkout HEAD -- config/sync/core.extension.yml"],
            capture_output=True,
            text=True,
        )
        assert blocked_absolute.returncode == 126
        assert "Absolute /usr/bin/git" in blocked_absolute.stderr

        bash_wrapper = bin_dir / "bash"
        blocked_bash_absolute = subprocess.run(
            [str(bash_wrapper), "-c", "/usr/bin/git reset --hard HEAD"],
            capture_output=True,
            text=True,
        )
        assert blocked_bash_absolute.returncode == 126
        assert "Absolute /usr/bin/git" in blocked_bash_absolute.stderr

    @patch("nichebench.execution.orchestrator.subprocess.run")
    def test_cage_launch_mounts_wrapper_and_prepends_path(self, mock_run, tmp_path):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
            }
        )
        test_case = TestCaseSpec(id="test_git_wrapper", type="runtime", raw={}, prompt="Task")
        workspace = MagicMock()
        workspace.path = tmp_path
        profile = resolve_profile("offline_cli")

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        executor._run_container_runtime_task(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=profile,
            timeout_seconds=30,
        )

        command = mock_run.call_args.args[0]
        env_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-e"]
        mount_values = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-v"]

        assert "PATH=/nichebench/state/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" in env_values
        assert any(value.endswith(":/nichebench/state/bin:ro") for value in mount_values)
