"""Executor-flow integration: host-level runtime slots."""

from __future__ import annotations

import contextlib
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.orchestrator import TestExecutor
from nichebench.execution.runtime import concurrency


def _make_executor(runtime_config: Dict[str, Any]) -> TestExecutor:
    mut_cfg = {"provider": "groq", "model": "test-model", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

    with (
        patch("nichebench.execution.orchestrator.get_config") as mock_config,
        patch.object(TestExecutor, "_load_system_prompt", return_value=None),
        patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
    ):
        mock_config.return_value.get_evaluation_config.return_value = runtime_config
        mock_config.return_value.get_model_string.side_effect = lambda cfg: f"{cfg['provider']}/{cfg['model']}"
        return TestExecutor(
            framework="drupal_runtime",
            category="runtime",
            mut_config=mut_cfg,
            judge_config=judge_cfg,
            network_config=network_cfg,
        )


def _runtime_case(test_id: str = "runtime_slots") -> TestCaseSpec:
    return TestCaseSpec(
        id=test_id,
        type="runtime",
        raw={"checks": [], "llm_judge": {"checklist": []}},
        prompt="Do the runtime task",
    )


def _run_flow(
    executor: TestExecutor,
    case: TestCaseSpec,
    container: Optional[Any] = None,
) -> Any:
    """Run the runtime flow with the cage and the scorer stubbed out."""
    if container is None:

        def container(**_kwargs: Any):
            return ("out", "in", "log", {}, "image", None, None)

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(executor, "_run_container_runtime_task_with_retry", side_effect=container),
    ):
        return executor.execute_runtime_test(case)  # type: ignore[attr-defined]


def test_execute_runtime_test_acquires_and_releases_the_host_slot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path / "slots"))
    executor = _make_executor({"runtime_mode": "cage", "runtime_max_concurrent_projects": 1})

    events: list[Any] = []
    original = concurrency.runtime_host_slots

    @contextlib.contextmanager
    def spy(*args: Any, **kwargs: Any):
        with original(*args, **kwargs) as slots:
            events.append(("enter", slots))
            yield slots
        events.append(("exit", None))

    monkeypatch.setattr(concurrency, "runtime_host_slots", spy)

    with patch("nichebench.execution.orchestrator.RuntimeScorer"):
        result = _run_flow(executor, _runtime_case())

    assert events[0] == ("enter", 1)
    assert events[-1] == ("exit", None)
    assert result.error is None


def test_workspace_cleanup_runs_before_the_slot_is_released(tmp_path: Path, monkeypatch):
    """The slot must still be held while the workspace is torn down."""
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path / "slots"))
    executor = _make_executor({"runtime_mode": "cage", "runtime_max_concurrent_projects": 1})

    order: list[str] = []
    original = concurrency.runtime_host_slots

    @contextlib.contextmanager
    def spy(*args: Any, **kwargs: Any):
        with original(*args, **kwargs) as slots:
            order.append("slot-enter")
            yield slots
        order.append("slot-exit")

    monkeypatch.setattr(concurrency, "runtime_host_slots", spy)

    with (
        patch("nichebench.execution.orchestrator.RuntimeScorer"),
        patch(
            "nichebench.execution.runtime.executor.cleanup.cleanup_workspace",
            side_effect=lambda **kwargs: order.append("cleanup"),
        ),
    ):
        _run_flow(executor, _runtime_case())

    assert order == ["slot-enter", "cleanup", "slot-exit"]


def test_slot_timeout_is_recorded_as_a_config_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path / "slots"))

    holding = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with concurrency.runtime_host_slots(1, timeout_seconds=10):
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert holding.wait(5), "the slot was never taken"

    executor = _make_executor(
        {
            "runtime_mode": "cage",
            "runtime_max_concurrent_projects": 1,
            "runtime_slot_timeout_seconds": 0.25,
        }
    )

    try:
        with patch("nichebench.execution.orchestrator.RuntimeScorer"):
            result = _run_flow(executor, _runtime_case())
    finally:
        release.set()
        thread.join(5)

    assert result.passed is False
    assert result.error_kind == "config_error"
    assert result.judge_output["error_kind"] == "config_error"
    assert "slot" in (result.error or "")


def _max_parallel_flows(tmp_path: Path, monkeypatch, max_slots: int) -> int:
    """Run two runtime flows at once and report the peak overlap."""
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path / "slots"))

    state = {"active": 0, "peak": 0}
    guard = threading.Lock()

    def container(**_kwargs: Any):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.4)
        with guard:
            state["active"] -= 1
        return ("out", "in", "log", {}, "image", None, None)

    config = {
        "runtime_mode": "cage",
        "runtime_max_concurrent_projects": max_slots,
        "runtime_enable_review_nudge": False,
    }
    executors = [_make_executor(config), _make_executor(config)]
    barrier = threading.Barrier(2)

    def worker(index: int) -> None:
        barrier.wait(10)
        _run_flow(executors[index], _runtime_case(f"runtime_{index}"), container=container)

    threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(2)]
    with patch("nichebench.execution.orchestrator.RuntimeScorer"):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

    return int(state["peak"])


def test_one_slot_serialises_two_runtime_flows(tmp_path: Path, monkeypatch):
    assert _max_parallel_flows(tmp_path, monkeypatch, 1) == 1


def test_two_slots_let_two_runtime_flows_overlap(tmp_path: Path, monkeypatch):
    assert _max_parallel_flows(tmp_path, monkeypatch, 2) == 2
