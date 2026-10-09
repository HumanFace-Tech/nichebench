"""Executor-flow integration: capture allocation, wiring, and references."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.orchestrator import TestExecutor
from nichebench.execution.runtime.capture import CaptureSession
from tests.unit.runtime.capture_helpers import make_session

MINIMAX_API_BASE = "https://api.minimax.io/v1"


def _make_executor(runtime_config: Dict[str, Any], results_outdir: Path) -> TestExecutor:
    mut_cfg = {"provider": "minimax", "model": "MiniMax-M3", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}
    with (
        patch("nichebench.execution.orchestrator.get_config") as mock_config,
        patch.object(TestExecutor, "_load_system_prompt", return_value=None),
        patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
    ):
        mock_config.return_value.get_evaluation_config.return_value = runtime_config
        mock_config.return_value.get_model_string.side_effect = lambda cfg: f"{cfg['provider']}/{cfg['model']}"
        executor = TestExecutor(
            framework="drupal_runtime",
            category="runtime",
            mut_config=mut_cfg,
            judge_config=judge_cfg,
            network_config=network_cfg,
        )
    executor.results_outdir = results_outdir
    return executor


def _runtime_case(test_id: str = "runtime_capture") -> TestCaseSpec:
    return TestCaseSpec(
        id=test_id,
        type="runtime",
        raw={"checks": [], "llm_judge": {"checklist": []}},
        prompt="Do the runtime task",
    )


def _capture_config(**extra: Any) -> Dict[str, Any]:
    config = {
        "runtime_mode": "cage",
        "runtime_capture_enable": True,
        "runtime_opencode_api_base": MINIMAX_API_BASE,
    }
    config.update(extra)
    return config


class TestCaptureAllocationInFlow:
    def test_capture_allocated_before_the_cage_launches(self, tmp_path):
        executor = _make_executor(_capture_config(), tmp_path / "results")
        allocated: Dict[str, Any] = {"seen": []}

        def fake_container(**kwargs: Any):
            allocated["seen"].append(kwargs)
            # 7-tuple: the retry wrapper adds retry_info.
            return ("out", "in", "log", {}, "image", None, None)

        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(executor, "_load_runtime_checks", return_value=[]),
            patch.object(executor, "_run_container_runtime_task_with_retry", side_effect=fake_container),
            patch("nichebench.execution.orchestrator.RuntimeScorer"),
        ):
            executor.execute_runtime_test(_runtime_case())

        # The archive existed before the container ran, so a hard launch failure
        # would still leave a record.
        assert allocated["seen"], "the cage was never invoked"
        labels = []
        for call in allocated["seen"]:
            assert call["capture"] is not None
            labels.append(call["attempt_label"])
        # First pass, then the optional review pass — each with its own label.
        assert labels[0] == "initial"
        assert set(labels) <= {"initial", "review_nudge"}
        assert list((tmp_path / "results" / "captures").iterdir())

    def test_capture_reference_lands_in_metadata_and_artifacts(self, tmp_path):
        executor = _make_executor(_capture_config(), tmp_path / "results")
        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(executor, "_load_runtime_checks", return_value=[]),
            patch.object(
                executor,
                "_run_container_runtime_task_with_retry",
                return_value=("out", "in", "log", {}, "image", None, None),
            ),
            patch("nichebench.execution.orchestrator.RuntimeScorer"),
        ):
            result = executor.execute_runtime_test(_runtime_case())

        reference = result.runtime_artifacts["capture.json"]
        assert reference["route"] == "minimax_m3"
        assert Path(reference["capture_path"]).exists()
        assert result.runtime_artifacts["metadata.json"]["runtime_capture"]["invocation_id"]

    def test_capture_reference_reaches_the_runtime_trace(self, tmp_path):
        executor = _make_executor(_capture_config(), tmp_path / "results")
        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(executor, "_load_runtime_checks", return_value=[]),
            patch.object(
                executor,
                "_run_container_runtime_task_with_retry",
                return_value=("out", "in", "log", {}, "image", None, None),
            ),
            patch("nichebench.execution.orchestrator.RuntimeScorer"),
        ):
            result = executor.execute_runtime_test(_runtime_case())

        trace = result.runtime_artifacts["runtime_trace.json"]
        assert trace["capture"]["route"] == "minimax_m3"
        # Only a reference travels in the trace; no payload is inlined.
        assert "events" not in trace["capture"]

    def test_capture_json_reference_is_persisted_in_the_task_folder(self, tmp_path):
        executor = _make_executor(_capture_config(), tmp_path / "results")
        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(executor, "_load_runtime_checks", return_value=[]),
            patch.object(
                executor,
                "_run_container_runtime_task_with_retry",
                return_value=("out", "in", "log", {}, "image", None, None),
            ),
            patch("nichebench.execution.orchestrator.RuntimeScorer"),
        ):
            result = executor.execute_runtime_test(_runtime_case())

        # The mocked scorer leaves MagicMock values in the artifact bundle; that
        # is orthogonal to capture, so strip them before persisting.
        for name in ("checks.json", "runtime_trace.json", "metadata.json"):
            value = result.runtime_artifacts.get(name)
            try:
                json.dumps(value)
            except TypeError:
                result.runtime_artifacts.pop(name)
        executor._save_runtime_artifacts(result)
        pointer = tmp_path / "results" / "runtime" / "runtime_capture" / "capture.json"
        assert pointer.exists()
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        assert payload["completeness"] in ("complete", "incomplete")
        assert payload["route"] == "minimax_m3"
        assert Path(payload["capture_path"]).exists()
        assert payload["archive_path"] == payload["capture_path"]

    def test_archive_survives_workspace_cleanup(self, tmp_path):
        executor = _make_executor(_capture_config(), tmp_path / "results")
        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(executor, "_load_runtime_checks", return_value=[]),
            patch.object(
                executor,
                "_run_container_runtime_task_with_retry",
                return_value=("out", "in", "log", {}, "image", None, None),
            ),
            patch("nichebench.execution.orchestrator.RuntimeScorer"),
        ):
            executor.execute_runtime_test(_runtime_case())

        archives = list((tmp_path / "results" / "captures").glob("*/capture.json"))
        assert archives
        # The archive root lives outside any task workspace directory.
        assert "captures" in archives[0].parts


class TestCaptureDisabledInFlow:
    def test_no_capture_directory_and_no_reference(self, tmp_path):
        executor = _make_executor({"runtime_mode": "cage"}, tmp_path / "results")
        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(executor, "_load_runtime_checks", return_value=[]),
            patch.object(
                executor,
                "_run_container_runtime_task_with_retry",
                return_value=("out", "in", "log", {}, "image", None, None),
            ),
            patch("nichebench.execution.orchestrator.RuntimeScorer"),
        ):
            result = executor.execute_runtime_test(_runtime_case("runtime_nocap"))

        assert not (tmp_path / "results" / "captures").exists()
        assert "capture.json" not in result.runtime_artifacts
        assert "capture" not in result.runtime_artifacts["runtime_trace.json"]
        assert "runtime_capture" not in result.runtime_artifacts["metadata.json"]

    def test_existing_runtime_artifacts_are_unchanged(self, tmp_path):
        executor = _make_executor({"runtime_mode": "cage"}, tmp_path / "results")
        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(executor, "_load_runtime_checks", return_value=[]),
            patch.object(
                executor,
                "_run_container_runtime_task_with_retry",
                return_value=("out", "in", "log", {}, "image", None, None),
            ),
            patch("nichebench.execution.orchestrator.RuntimeScorer"),
        ):
            result = executor.execute_runtime_test(_runtime_case("runtime_plain"))

        assert result.mut_output == "out"
        assert set(result.runtime_artifacts) >= {"run.log", "metadata.json", "checks.json"}


class TestCaptureFinalisationOnFailure:
    def test_executor_exception_still_finalises_the_capture(self, tmp_path):
        executor = _make_executor(_capture_config(), tmp_path / "results")
        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(executor, "_load_runtime_checks", return_value=[]),
            patch.object(
                executor,
                "_run_container_runtime_task_with_retry",
                side_effect=RuntimeError("Container OpenCode command failed with exit 2"),
            ),
        ):
            result = executor.execute_runtime_test(_runtime_case("runtime_fail"))

        archives = list((tmp_path / "results" / "captures").glob("*/capture.json"))
        assert archives, "capture descriptor must exist even on failure"
        descriptor = json.loads(archives[0].read_text(encoding="utf-8"))
        assert descriptor["reasons"]
        assert result.error

    def test_capture_finalisation_never_raises_into_the_flow(self, tmp_path):
        session = make_session(tmp_path / "results")
        with patch.object(
            CaptureSession,
            "finalise",
            side_effect=RuntimeError("archive exploded"),
        ):
            from nichebench.execution.runtime.executor import cleanup as executor_cleanup

            executor_cleanup.finalize_capture(capture=session, current_stage="agent_execution")


class TestRouteResolution:
    def test_minimax_route_is_supported(self):
        from nichebench.execution.runtime.cage.mixin import resolve_capture_route

        assert (
            resolve_capture_route(
                {
                    "runtime_opencode_api_base": MINIMAX_API_BASE,
                    "runtime_opencode_provider_name": "minimax",
                }
            )
            == "minimax_m3"
        )

    def test_non_minimax_endpoint_is_unsupported(self):
        from nichebench.execution.runtime.cage.mixin import resolve_capture_route

        assert resolve_capture_route({"runtime_opencode_api_base": "http://192.168.1.111:9292"}) is None

    def test_unsupported_route_fails_the_run_before_launch(self, tmp_path):
        """An unsupported route must fail loudly rather than capture partially."""
        from nichebench.execution.runtime.capture import CaptureUnsupported

        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_capture_enable": True,
                "runtime_opencode_api_base": "http://192.168.1.111:9292",
            },
            tmp_path / "results",
        )
        with pytest.raises(CaptureUnsupported, match="no capture route"):
            executor._allocate_capture_session(
                {
                    "runtime_capture_enable": True,
                    "runtime_opencode_api_base": "http://192.168.1.111:9292",
                },
                "t",
            )

    def test_other_supported_route_is_reported_as_unsupported(self, tmp_path):
        from nichebench.execution.runtime.capture import CaptureRun, CaptureSettings, CaptureUnsupported

        settings = CaptureSettings.from_runtime_config({"runtime_capture_enable": True}, route="qwen_local")
        with pytest.raises(CaptureUnsupported, match="does not support route"):
            CaptureRun.allocate(tmp_path, settings, "t")

    def test_minimal_retention_is_rejected_up_front(self, tmp_path):
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_capture_enable": True,
                "runtime_artifact_retention": "minimal",
                "runtime_opencode_api_base": MINIMAX_API_BASE,
            },
            tmp_path / "results",
        )
        with pytest.raises(Exception, match="minimal"):
            executor._allocate_capture_session(
                {
                    "runtime_capture_enable": True,
                    "runtime_artifact_retention": "minimal",
                    "runtime_opencode_api_base": MINIMAX_API_BASE,
                },
                "t",
            )
