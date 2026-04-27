"""Unit tests for runtime scoring in execute_runtime_test()."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.executor import TestExecutor


def _make_executor(runtime_config=None):
    mut_cfg = {"provider": "groq", "model": "test-model", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

    with (
        patch("nichebench.core.executor.get_config") as mock_config,
        patch.object(TestExecutor, "_load_system_prompt", return_value=None),
        patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
    ):
        mock_config.return_value.get_evaluation_config.return_value = runtime_config or {}
        mock_config.return_value.get_model_string.side_effect = lambda cfg: f"{cfg['provider']}/{cfg['model']}"
        return TestExecutor(
            framework="drupal_runtime",
            category="runtime",
            mut_config=mut_cfg,
            judge_config=judge_cfg,
            network_config=network_cfg,
        )


def test_execute_runtime_test_does_not_auto_pass_when_deterministic_check_fails():
    executor = _make_executor({"runtime_mode": "cage"})
    test_case = TestCaseSpec(
        id="runtime_fail_check",
        type="runtime",
        raw={
            "checks": [
                {"op": "file_exists", "path": "web/modules/custom/example.module", "label": "module file exists"}
            ]
        },
        prompt="Do the runtime task",
    )

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", "run log", {}, "image", None),
        ),
    ):
        result = executor.execute_runtime_test(test_case)

    assert result.error is None
    assert result.passed is False
    assert result.judge_output["deterministic_score"] == pytest.approx(0.0)
    assert "checks.json" in result.runtime_artifacts
    checks = result.runtime_artifacts["checks.json"]["deterministic"]
    assert len(checks) == 1
    assert checks[0]["passed"] is False


def test_execute_runtime_test_uses_runtime_judge_for_hybrid_score():
    executor = _make_executor({"runtime_mode": "cage"})
    test_case = TestCaseSpec(
        id="runtime_with_judge",
        type="runtime",
        raw={
            "checks": [],
            "llm_judge": {"checklist": [{"id": "c1", "question": "Did it work?", "weight": 1.0}]},
            "scoring": {"deterministic_weight": 0.5, "llm_weight": 0.5, "threshold": 0.7},
        },
        prompt="Do the runtime task",
    )

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", "run log", {}, "image", None),
        ),
        patch.object(
            executor.judge_runner,
            "evaluate_test",
            return_value=({"overall_score": 0.2}, False),
        ) as mock_judge,
    ):
        result = executor.execute_runtime_test(test_case)

    mock_judge.assert_called_once()
    assert result.judge_output["judge_score"] == pytest.approx(0.2)
    assert result.judge_output["hybrid_score"] == pytest.approx(0.6)
    assert result.passed is False


def test_execute_runtime_test_uses_workspace_lifecycle_with_task_branch_preferred(tmp_path):
    executor = _make_executor({"runtime_mode": "cage", "runtime_timeout_seconds": 123})
    test_case = TestCaseSpec(
        id="runtime_workspace",
        type="runtime",
        raw={
            "source": {"task_branch": "task/runtime_workspace", "base_branch": "main"},
            "environment": {"setup_mode": "db_snapshot"},
        },
        prompt="Do the runtime task",
        file_path=str(tmp_path / "manifest.yaml"),
    )

    mock_workspace = MagicMock()
    mock_workspace.path = tmp_path / "workspace"
    mock_workspace.ddev_project_name = "nb-runtime-workspace"

    mock_score = type(
        "Score",
        (),
        {
            "deterministic_score": 1.0,
            "judge_score": None,
            "final_score": 1.0,
            "passed": True,
        },
    )()

    with (
        patch("nichebench.core.executor.Workspace", return_value=mock_workspace) as mock_workspace_cls,
        patch("nichebench.core.executor.find_git_root", return_value=tmp_path) as mock_find_root,
        patch("nichebench.core.executor.resolve_branch_to_sha", return_value="sha-task") as mock_resolve_sha,
        patch("nichebench.core.executor.validate_runtime_testcase"),
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", "run log", {}, "image", None),
        ),
        patch("nichebench.core.executor.RuntimeScorer") as mock_scorer_cls,
    ):
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.run_deterministic_checks.return_value = []
        mock_scorer.compute_hybrid_score.return_value = mock_score

        executor.execute_runtime_test(test_case)

    mock_find_root.assert_called_once_with(Path(test_case.file_path))
    mock_resolve_sha.assert_called_once_with("task/runtime_workspace", tmp_path)
    mock_workspace_cls.assert_called_once_with(base_path=Path("workspaces"), task_id="runtime_workspace")
    mock_workspace.create.assert_called_once_with(source_path=tmp_path, sha="sha-task")
    mock_workspace.ddev_start.assert_called_once_with(
        setup_mode="db_snapshot",
        timeout=123,
        post_setup_commands=None,
    )


def test_execute_runtime_test_falls_back_to_temp_workspace_without_source_environment(tmp_path):
    executor = _make_executor({"runtime_mode": "cage"})
    test_case = TestCaseSpec(id="runtime_temp_ws", type="runtime", raw={}, prompt="Do the runtime task")
    temp_workspace = tmp_path / "temp-runtime"
    temp_workspace.mkdir()

    mock_score = type(
        "Score",
        (),
        {
            "deterministic_score": 1.0,
            "judge_score": None,
            "final_score": 1.0,
            "passed": True,
        },
    )()

    with (
        patch("nichebench.core.executor.Workspace") as mock_workspace_cls,
        patch("nichebench.core.executor.tempfile.mkdtemp", return_value=str(temp_workspace)),
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", "run log", {}, "image", None),
        ),
        patch("nichebench.core.executor.RuntimeScorer") as mock_scorer_cls,
    ):
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.run_deterministic_checks.return_value = []
        mock_scorer.compute_hybrid_score.return_value = mock_score

        executor.execute_runtime_test(test_case)

    mock_workspace_cls.assert_not_called()


def test_execute_runtime_test_keeps_workspace_when_runtime_keep_workspaces_enabled(tmp_path):
    executor = _make_executor({"runtime_mode": "cage", "runtime_keep_workspaces": True})
    test_case = TestCaseSpec(
        id="runtime_keep_ws",
        type="runtime",
        raw={
            "source": {"task_branch": "task/runtime_keep_ws"},
            "environment": {"setup_mode": "config_import"},
        },
        prompt="Do the runtime task",
        file_path=str(tmp_path / "manifest.yaml"),
    )

    mock_workspace = MagicMock()
    mock_workspace.path = tmp_path / "workspace"
    mock_workspace.ddev_project_name = "nb-runtime-keep"

    mock_score = type(
        "Score",
        (),
        {
            "deterministic_score": 1.0,
            "judge_score": None,
            "final_score": 1.0,
            "passed": True,
        },
    )()

    with (
        patch("nichebench.core.executor.Workspace", return_value=mock_workspace),
        patch("nichebench.core.executor.find_git_root", return_value=tmp_path),
        patch("nichebench.core.executor.resolve_branch_to_sha", return_value="sha-task"),
        patch("nichebench.core.executor.validate_runtime_testcase"),
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", "run log", {}, "image", None),
        ),
        patch("nichebench.core.executor.RuntimeScorer") as mock_scorer_cls,
    ):
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.run_deterministic_checks.return_value = []
        mock_scorer.compute_hybrid_score.return_value = mock_score

        executor.execute_runtime_test(test_case)

    mock_workspace.cleanup.assert_called_once_with(timeout=1800, remove_workspace=False)


def test_execute_runtime_test_adds_failing_critical_check_for_disallowed_tools():
    executor = _make_executor({"runtime_mode": "cage", "runtime_tool_allowlist_enforce": True})
    test_case = TestCaseSpec(id="runtime_tool_guard", type="runtime", raw={"checks": []}, prompt="Do the runtime task")
    trajectory = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "bash"}},
                    {"function": {"name": "apply_patch"}},
                ],
            }
        ]
    }

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", "run log", {}, "image", trajectory),
        ),
    ):
        result = executor.execute_runtime_test(test_case)

    checks = result.runtime_artifacts["checks.json"]["deterministic"]
    guard_checks = [c for c in checks if c["name"] == "tool_allowlist_guard"]
    assert len(guard_checks) == 1
    assert guard_checks[0]["critical"] is True
    assert guard_checks[0]["passed"] is False
    assert result.judge_output["deterministic_gate_passed"] is False
    assert result.passed is False


def test_execute_runtime_test_non_blocking_tool_guard_records_disallowed_but_passes():
    """Test that when enforce=False (default), disallowed tools are recorded but check passes."""
    executor = _make_executor({"runtime_mode": "cage", "runtime_tool_allowlist_enforce": False})
    test_case = TestCaseSpec(
        id="runtime_tool_guard_nonblock", type="runtime", raw={"checks": []}, prompt="Do the runtime task"
    )
    trajectory = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "read"}},
                    {"function": {"name": "repo_browser.open_file"}},
                ],
            }
        ]
    }

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", "run log", {}, "image", trajectory),
        ),
    ):
        result = executor.execute_runtime_test(test_case)

    checks = result.runtime_artifacts["checks.json"]["deterministic"]
    guard_checks = [c for c in checks if c["name"] == "tool_allowlist_guard"]
    assert len(guard_checks) == 1
    # Non-blocking: check passes even though disallowed tools were used
    assert guard_checks[0]["passed"] is True
    # But details still record the disallowed tools
    assert "repo_browser.open_file" in guard_checks[0]["details"]["disallowed_tools"]
    assert guard_checks[0]["details"]["enforce_mode"] is False
    # Non-critical in non-blocking mode
    assert guard_checks[0]["critical"] is False
    # Gate should still pass since check passed
    assert result.judge_output["deterministic_gate_passed"] is True


def test_execute_runtime_test_rejected_tool_attempts_recorded_in_non_blocking_mode():
    """Test that rejected tool attempts (from run.log parsing) are recorded even in non-blocking mode."""
    executor = _make_executor({"runtime_mode": "cage", "runtime_tool_allowlist_enforce": False})
    test_case = TestCaseSpec(
        id="runtime_tool_rejected", type="runtime", raw={"checks": []}, prompt="Do the runtime task"
    )
    trajectory = None  # No trajectory, but rejected attempts from run.log

    # Simulate run.log with rejected tool attempts
    run_log = (
        "ERROR: attempted to call tool 'repo_browser.open_file' which was not in request.tools\n"
        "ERROR: attempted to call tool 'web_search' which was not in request.tools"
    )

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", run_log, {}, "image", trajectory),
        ),
    ):
        result = executor.execute_runtime_test(test_case)

    checks = result.runtime_artifacts["checks.json"]["deterministic"]
    guard_checks = [c for c in checks if c["name"] == "tool_allowlist_guard"]
    assert len(guard_checks) == 1
    # Check passes in non-blocking mode
    assert guard_checks[0]["passed"] is True
    # Rejected attempts are recorded
    rejected = guard_checks[0]["details"]["rejected_tool_attempts"]
    assert len(rejected) == 2
    rejected_names = {r["tool_name"] for r in rejected}
    assert "repo_browser.open_file" in rejected_names
    assert "web_search" in rejected_names


def test_execute_runtime_test_strict_mode_fails_on_disallowed_tools():
    """Test that when enforce=True, any disallowed tool causes check failure."""
    executor = _make_executor({"runtime_mode": "cage", "runtime_tool_allowlist_enforce": True})
    test_case = TestCaseSpec(id="runtime_tool_strict", type="runtime", raw={"checks": []}, prompt="Do the runtime task")
    trajectory = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "read"}},
                    {"function": {"name": "some_disallowed_tool"}},
                ],
            }
        ]
    }

    mock_score = type(
        "Score",
        (),
        {
            "deterministic_score": 0.5,
            "judge_score": None,
            "final_score": 0.5,
            "passed": False,
        },
    )()

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(
            executor,
            "_run_container_runtime_task",
            return_value=("mut output", "user input", "run log", {}, "image", trajectory),
        ),
        patch("nichebench.core.executor.RuntimeScorer") as mock_scorer_cls,
    ):
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.run_deterministic_checks.return_value = []
        mock_scorer.compute_hybrid_score.return_value = mock_score

        result = executor.execute_runtime_test(test_case)

    # Check passes in non-blocking mode
    assert result.passed is False
    assert result.judge_output["deterministic_gate_passed"] is False


def test_execute_runtime_test_two_pass_review_nudge_enabled(tmp_path):
    """Two-pass flow: when runtime_enable_review_nudge is true, second pass runs and uses its output."""
    executor = _make_executor(
        {
            "runtime_mode": "cage",
            "runtime_enable_review_nudge": True,
            "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
        }
    )
    test_case = TestCaseSpec(
        id="runtime_review_nudge",
        type="runtime",
        raw={"checks": [], "llm_judge": {"checklist": []}},
        prompt="Do the runtime task",
    )
    # Create a TASK.md in tmp workspace
    task_md = tmp_path / "TASK.md"
    task_md.write_text("Initial task", encoding="utf-8")

    first_pass_output = "first pass output"
    second_pass_output = "second pass output"
    first_pass_log = "first pass log"
    second_pass_log = "second pass log"

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(
            executor,
            "_run_container_runtime_task_with_retry",
            side_effect=[
                (first_pass_output, "user input", first_pass_log, {}, "image", None, None),
                (second_pass_output, "user input 2", second_pass_log, {}, "image", None, None),
            ],
        ) as mock_retry,
        patch("nichebench.core.executor.RuntimeScorer") as mock_scorer_cls,
    ):
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.run_deterministic_checks.return_value = []
        mock_scorer.compute_hybrid_score.return_value = MagicMock(
            deterministic_score=1.0,
            judge_score=1.0,
            final_score=1.0,
            passed=True,
        )

        # Patch _load_review_nudge to return a nudge
        with patch.object(executor, "_load_review_nudge", return_value="REVIEW_NUDGE_TEXT"):
            result = executor.execute_runtime_test(test_case)

    # Verify retry wrapper was called twice: once for first pass, once for review pass
    assert mock_retry.call_count == 2
    # Final output should be from second pass
    assert result.mut_output == second_pass_output
    # First pass output should be stored in artifacts
    assert "review_pass_output" in result.runtime_artifacts
    assert result.runtime_artifacts["review_pass_output"]["first_pass_output"] == first_pass_output
    assert result.runtime_artifacts["review_pass_output"]["first_pass_run_log"] == first_pass_log
    # review_pass_info should be in metadata
    metadata = result.runtime_artifacts["metadata.json"]
    assert "review_pass_info" in metadata
    assert metadata["review_pass_info"]["attempted"] is True


def test_execute_runtime_test_second_pass_uses_review_nudge_as_task_input_override(tmp_path):
    """Second pass uses review nudge as task_input_override via the retry wrapper."""
    executor = _make_executor(
        {
            "runtime_mode": "cage",
            "runtime_enable_review_nudge": True,
            "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
        }
    )
    test_case = TestCaseSpec(
        id="runtime_review_nudge_override",
        type="runtime",
        raw={"checks": [], "llm_judge": {"checklist": []}},
        prompt="Do the runtime task",
    )
    # Create a TASK.md in tmp workspace
    task_md = tmp_path / "TASK.md"
    task_md.write_text("Initial task content", encoding="utf-8")
    original_task_md_content = "Initial task content"

    first_pass_output = "first pass output"
    second_pass_output = "second pass output"

    review_nudge_text = "REVIEW_NUDGE_TEXT_IS_HERE"

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(
            executor,
            "_run_container_runtime_task_with_retry",
            side_effect=[
                (first_pass_output, "user input", "first pass log", {}, "image", None, None),
                (second_pass_output, "user input 2", "second pass log", {}, "image", None, None),
            ],
        ) as mock_retry,
        patch("nichebench.core.executor.RuntimeScorer") as mock_scorer_cls,
    ):
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.run_deterministic_checks.return_value = []
        mock_scorer.compute_hybrid_score.return_value = MagicMock(
            deterministic_score=1.0,
            judge_score=1.0,
            final_score=1.0,
            passed=True,
        )

        with patch.object(executor, "_load_review_nudge", return_value=review_nudge_text):
            executor.execute_runtime_test(test_case)

    # Verify retry wrapper was called twice
    assert mock_retry.call_count == 2
    # Second call must have task_input_override=review_nudge_text
    second_call_kwargs = mock_retry.call_args_list[1].kwargs
    assert second_call_kwargs.get("task_input_override") == review_nudge_text

    # TASK.md should NOT have been modified (should still have original content)
    assert task_md.read_text(encoding="utf-8") == original_task_md_content


def test_execute_runtime_test_review_nudge_disabled_no_second_pass(tmp_path):
    """When runtime_enable_review_nudge is false, no second pass runs."""
    executor = _make_executor(
        {
            "runtime_mode": "cage",
            "runtime_enable_review_nudge": False,
            "runtime_container_image": "ghcr.io/opencode-ai/opencode:v0.14.0",
        }
    )
    test_case = TestCaseSpec(
        id="runtime_no_review_nudge",
        type="runtime",
        raw={"checks": [], "llm_judge": {"checklist": []}},
        prompt="Do the runtime task",
    )
    task_md = tmp_path / "TASK.md"
    task_md.write_text("Initial task", encoding="utf-8")

    first_pass_output = "only pass output"

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(
            executor,
            "_run_container_runtime_task_with_retry",
            return_value=(first_pass_output, "user input", "run log", {}, "image", None, None),
        ) as mock_first_pass,
        patch.object(
            executor,
            "_run_container_runtime_task",
        ) as mock_second_pass,
        patch("nichebench.core.executor.RuntimeScorer") as mock_scorer_cls,
    ):
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.run_deterministic_checks.return_value = []
        mock_scorer.compute_hybrid_score.return_value = MagicMock(
            deterministic_score=1.0,
            judge_score=1.0,
            final_score=1.0,
            passed=True,
        )

        result = executor.execute_runtime_test(test_case)

    # First pass should be called once
    assert mock_first_pass.call_count == 1
    # Second pass should NOT be called
    assert mock_second_pass.call_count == 0
    # Output should be from first pass
    assert result.mut_output == first_pass_output
    # No review_pass_output in artifacts
    assert "review_pass_output" not in result.runtime_artifacts


def test_review_pass_uses_retry_wrapper(tmp_path):
    """Review pass goes through _run_container_runtime_task_with_retry, not _run_container_runtime_task."""
    executor = _make_executor(
        {
            "runtime_mode": "cage",
            "runtime_enable_review_nudge": True,
        }
    )
    test_case = TestCaseSpec(
        id="runtime_review_retry_wrapper",
        type="runtime",
        raw={"checks": []},
        prompt="Do the runtime task",
    )

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(
            executor,
            "_run_container_runtime_task_with_retry",
            side_effect=[
                ("first output", "user input", "first log", {}, "image", None, None),
                ("review output", "user input 2", "review log", {}, "image", None, None),
            ],
        ) as mock_retry,
        patch.object(executor, "_run_container_runtime_task") as mock_direct,
        patch.object(executor, "_load_review_nudge", return_value="REVIEW_NUDGE"),
        patch("nichebench.core.executor.RuntimeScorer") as mock_scorer_cls,
    ):
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.run_deterministic_checks.return_value = []
        mock_scorer.compute_hybrid_score.return_value = MagicMock(
            deterministic_score=1.0,
            judge_score=None,
            final_score=1.0,
            passed=True,
        )
        executor.execute_runtime_test(test_case)

    # Retry wrapper must be called twice (first pass + review pass)
    assert mock_retry.call_count == 2
    # _run_container_runtime_task must NOT be called directly
    assert mock_direct.call_count == 0
    # Second call to retry wrapper must carry task_input_override=review nudge
    second_call_kwargs = mock_retry.call_args_list[1].kwargs
    assert second_call_kwargs.get("task_input_override") == "REVIEW_NUDGE"


class TestJudgeSampling:
    def _make_judge_test_case(self):
        return TestCaseSpec(
            id="runtime_judge_sampling",
            type="runtime",
            raw={
                "checks": [],
                "llm_judge": {"checklist": [{"id": "c1", "question": "Did it work?", "weight": 1.0}]},
                "scoring": {"deterministic_weight": 0.5, "llm_weight": 0.5, "threshold": 0.7},
            },
            prompt="Do the runtime task",
        )

    def test_judge_called_once_when_samples_is_1(self):
        executor = _make_executor({"runtime_mode": "cage", "runtime_judge_samples": 1})
        test_case = self._make_judge_test_case()

        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(
                executor,
                "_run_container_runtime_task",
                return_value=("mut output", "user input", "run log", {}, "image", None),
            ),
            patch.object(
                executor.judge_runner,
                "evaluate_test",
                return_value=({"overall_score": 0.2}, False),
            ) as mock_judge,
        ):
            result = executor.execute_runtime_test(test_case)

        assert mock_judge.call_count == 1
        assert "judge_sample_scores" not in result.judge_output["runtime_judge"]

    def test_judge_called_n_times_when_samples_is_3(self):
        executor = _make_executor({"runtime_mode": "cage", "runtime_judge_samples": 3})
        test_case = self._make_judge_test_case()

        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(
                executor,
                "_run_container_runtime_task",
                return_value=("mut output", "user input", "run log", {}, "image", None),
            ),
            patch.object(
                executor.judge_runner,
                "evaluate_test",
                side_effect=[
                    ({"overall_score": 0.4}, False),
                    ({"overall_score": 0.8}, False),
                    ({"overall_score": 0.6}, False),
                ],
            ) as mock_judge,
        ):
            result = executor.execute_runtime_test(test_case)

        assert mock_judge.call_count == 3
        assert result.judge_output["judge_score"] == pytest.approx(0.6)
        assert result.judge_output["runtime_judge"]["judge_sample_scores"] == [0.4, 0.8, 0.6]
        assert result.judge_output["runtime_judge"]["judge_sample_median"] == pytest.approx(0.6)

    def test_judge_samples_defaults_to_1_when_not_configured(self):
        executor = _make_executor({"runtime_mode": "cage"})
        test_case = self._make_judge_test_case()

        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(
                executor,
                "_run_container_runtime_task",
                return_value=("mut output", "user input", "run log", {}, "image", None),
            ),
            patch.object(
                executor.judge_runner,
                "evaluate_test",
                return_value=({"overall_score": 0.2}, False),
            ) as mock_judge,
        ):
            executor.execute_runtime_test(test_case)

        assert mock_judge.call_count == 1

    def test_judge_samples_clamped_to_minimum_of_1(self):
        executor = _make_executor({"runtime_mode": "cage", "runtime_judge_samples": 0})
        test_case = self._make_judge_test_case()

        with (
            patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
            patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
            patch.object(executor, "_inject_task_markdown"),
            patch.object(
                executor,
                "_run_container_runtime_task",
                return_value=("mut output", "user input", "run log", {}, "image", None),
            ),
            patch.object(
                executor.judge_runner,
                "evaluate_test",
                return_value=({"overall_score": 0.2}, False),
            ) as mock_judge,
        ):
            executor.execute_runtime_test(test_case)

        assert mock_judge.call_count == 1
