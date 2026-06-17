"""High-level execute_runtime_test orchestration.

Provides the main execute_runtime_test method that orchestrates the full
runtime lifecycle by delegating to stage helpers and specialized modules.

This module owns:
    - execute_runtime_test method orchestration
    - Stage sequencing and error handling wrapper
    - Result construction and initialization

This module does NOT own:
    - RuntimeExecutionMixin helper methods (see mixin.py)
    - Stage helper implementations (see stages.py)
    - Review nudge second-pass logic (see review_nudge.py)
    - Catastrophic failure short-circuit (see failure_shortcut.py)
    - Cleanup handling (see cleanup.py)

Trace stage names (preserved exactly):
    - config_resolution
    - workspace_setup
    - environment_bootstrap
    - agent_execution
    - deterministic_checks
    - judge_scoring
    - artifact_finalization
    - cleanup

Usage:
    execute_runtime_test is called by TestExecutor (via RuntimeExecutionMixin).
    This method should be kept as thin as possible, delegating to specialized
    helpers while preserving exact stage names and execution order.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.diagnostics.trace import RuntimeTrace
from nichebench.execution.result import TestResult
from nichebench.execution.runtime.executor import cleanup as executor_cleanup
from nichebench.execution.runtime.executor import (
    failure_shortcut as executor_failure_shortcut,
)
from nichebench.execution.runtime.executor import review_nudge as executor_review_nudge
from nichebench.execution.runtime.executor import stages as executor_stages

# Re-import mixin for use in flow (needed for _build_tool_allowlist_check etc.)
from nichebench.execution.runtime.executor.mixin import RuntimeExecutionMixin


def _executor_globals() -> Any:
    """Return orchestrator module so legacy patch points stay effective."""
    from nichebench.execution import orchestrator

    return orchestrator


def execute_runtime_test(self, test_case: TestCaseSpec, trial: int = 0) -> TestResult:
    """Execute a runtime test case and capture the full artifact bundle.

    Orchestrates the full runtime lifecycle:
    1. Workspace setup (DDEV project creation + config import)
    2. Environment bootstrap (preflight checks, TASK.md injection, hints)
    3. Agent execution (cage container with island topology)
    4. Optional two-pass review nudge (second MUT pass with quality prompt)
    5. Deterministic checks (file existence, grep, drush, static analysis)
    6. LLM judge scoring (hybrid deterministic + judge score)
    7. Artifact persistence and cleanup

    Args:
        self: RuntimeExecutionMixin instance
        test_case: Test case specification
        trial: Trial number (default 0)

    Returns:
        TestResult with runtime artifacts, judge output, and pass/fail status
    """
    result = TestResult(self.framework, self.category, test_case, self.mut_model_str, self.judge_model_str)
    diagnostics_enabled = bool(self.evaluation_config.get("runtime_enable_diagnostics", True))
    trace = RuntimeTrace(test_id=test_case.id)

    # Stage: config_resolution  # noqa: ERA001
    executor_stages.stage_config_resolution(trace, self.evaluation_config)
    runtime_config = self.evaluation_config
    runtime_mode = str(runtime_config.get("runtime_mode", "cage"))
    effective_runtime_mode = "cage" if runtime_mode in ("cage", "container") else runtime_mode
    runtime_timeout_seconds, keep_workspace, _ = executor_stages.resolve_runtime_config(runtime_config)

    workspace_dir: Optional[Path] = None
    runtime_hints_path: Optional[Path] = None
    # Initialise workspace to None so that handle_exception() / cleanup_workspace()
    # can be invoked even if a manifest-validation failure happens before
    # workspace is assigned in the try-block below.
    workspace: Any = None
    source = test_case.raw.get("source") if test_case.raw else None
    environment = test_case.raw.get("environment") if test_case.raw else None
    use_runtime_workspace = isinstance(source, dict) and isinstance(environment, dict)

    class _RuntimeWorkspace:
        def __init__(self, path: Path):
            self.path = path
            self.ddev_project_name = ""

    profile = _executor_globals().resolve_profile("offline_cli")
    current_stage = "config_resolution"
    try:
        # Stage: workspace_setup  # noqa: ERA001
        current_stage = "workspace_setup"
        if use_runtime_workspace:
            assert isinstance(source, dict)
            assert isinstance(environment, dict)
            _executor_globals().validate_runtime_testcase(test_case)
            file_path = Path(test_case.file_path) if test_case.file_path else Path.cwd()
            repo_root = _executor_globals().find_git_root(file_path)
            branch_name = source.get("task_branch") or source.get("base_branch")
            if branch_name:
                test_case.resolved_sha = _executor_globals().resolve_branch_to_sha(branch_name, repo_root)
            workspace = _executor_globals().Workspace(base_path=Path("workspaces"), task_id=test_case.id)
        else:
            workspace_dir = Path(tempfile.mkdtemp(prefix=f"nichebench-runtime-{test_case.id}-"))
            workspace = _RuntimeWorkspace(workspace_dir)

        workspace_dir = executor_stages.stage_workspace_setup(
            trace=trace,
            use_runtime_workspace=use_runtime_workspace,
            workspace=workspace,
            test_case=test_case,
            repo_root=repo_root if use_runtime_workspace else None,
            runtime_timeout_seconds=runtime_timeout_seconds,
            pre_existing_workspace_dir=workspace_dir if not use_runtime_workspace else None,
        )

        # Stage: environment_bootstrap  # noqa: ERA001
        current_stage = "environment_bootstrap"
        runtime_hints_path = executor_stages.stage_environment_bootstrap(
            trace=trace,
            workspace_dir=workspace_dir,
            runtime_config=runtime_config,
            runtime_mode=runtime_mode,
            effective_runtime_mode=effective_runtime_mode,
            mixin=self,
            test_case=test_case,
        )
        checks_config = self._load_runtime_checks(test_case)

        # Stage: agent_execution  # noqa: ERA001
        current_stage = "agent_execution"
        trace.stage_start(current_stage)
        if effective_runtime_mode == "cage":
            (
                mut_output,
                user_input,
                run_log,
                island_topology,
                effective_image,
                trajectory,
                retry_info,
            ) = self._run_container_runtime_task_with_retry(
                test_case=test_case,
                workspace=workspace,
                agent_manifest=test_case.raw.get("agent", {}),
                runtime_config=runtime_config,
                profile=profile,
                timeout_seconds=runtime_timeout_seconds,
            )
        else:
            raise _executor_globals().ValidationError(f"Unsupported runtime mode: {effective_runtime_mode}")

        # Two-pass review nudge: run a second MUT pass with review nudge as fresh user message
        enable_review_nudge = bool(runtime_config.get("runtime_enable_review_nudge", True))
        review_pass_info: Optional[Dict[str, Any]] = None
        first_pass_mut_output = mut_output
        first_pass_run_log = run_log

        first_pass_meaningful = executor_review_nudge.detect_meaningful_first_pass(mut_output, trajectory, run_log)

        if executor_review_nudge.should_run_review_nudge(
            enable_review_nudge, effective_runtime_mode, first_pass_meaningful
        ):
            review_nudge = self._load_review_nudge()
            if review_nudge:
                review_pass_info = executor_review_nudge.build_review_pass_info(
                    first_pass_mut_output, first_pass_run_log
                )
                try:
                    (
                        mut_output,
                        user_input,
                        run_log,
                        island_topology,
                        effective_image,
                        trajectory,
                        _,
                    ) = executor_review_nudge.run_review_nudge_pass(
                        mixin=self,
                        test_case=test_case,
                        workspace=workspace,
                        runtime_config=runtime_config,
                        profile=profile,
                        runtime_timeout_seconds=runtime_timeout_seconds,
                        review_nudge=review_nudge,
                    )
                except Exception as exc:
                    mut_output, run_log = executor_review_nudge.handle_review_pass_failure(
                        review_pass_info, first_pass_mut_output, first_pass_run_log, exc
                    )

        trace.stage_end(
            "agent_execution",
            "passed",
            {
                "review_nudge_attempted": bool(review_pass_info),
                "review_nudge_failed": bool(review_pass_info and review_pass_info.get("failed")),
                "review_nudge_error": review_pass_info.get("error") if review_pass_info else None,
                "mut_output_excerpt": (mut_output or "")[:300],
            },
        )

        result.user_input = user_input
        result.mut_output = mut_output

        # Parse rejected tool attempts from run.log
        rejected_tool_attempts = self._parse_rejected_tool_attempts(run_log)

        result.runtime_artifacts = {
            "run.log": run_log,
            "metadata.json": self._build_runtime_metadata(
                test_case=test_case,
                profile=profile,
                runtime_mode=runtime_mode,
                runtime_config=runtime_config,
                workspace=workspace,
                island_topology=island_topology,
                effective_image=effective_image,
                retry_info=retry_info,
                review_pass_info=review_pass_info,
            ),
        }
        metadata = result.runtime_artifacts["metadata.json"]
        if isinstance(metadata, dict):
            metadata["runtime_hints_enabled"] = bool(runtime_hints_path)
            if runtime_hints_path is not None:
                metadata["runtime_hints_file"] = str(runtime_hints_path)
        # Store first pass output in artifacts when review nudge was used
        if review_pass_info:
            result.runtime_artifacts["review_pass_output"] = {
                "first_pass_output": review_pass_info.get("first_pass_output", ""),
                "first_pass_run_log": review_pass_info.get("first_pass_run_log", ""),
            }
        if (workspace_dir / ".git").exists():
            try:
                if hasattr(workspace, "capture_final_diff"):
                    result.runtime_artifacts["final.diff"] = workspace.capture_final_diff(test_case.resolved_sha)
            except Exception as exc:
                # Record the failure so diagnostics can see the diff was
                # missing for a recoverable reason, not because the agent
                # produced no changes.
                result.runtime_artifacts["final_diff_error"] = str(exc)
                result.runtime_artifacts.pop("final.diff", None)
        else:
            result.runtime_artifacts["final_diff_error"] = "no_git_directory"
        if trajectory is not None:
            result.runtime_artifacts["trajectory.json"] = trajectory

        # --- Catastrophic failure short-circuit ---
        catastrophic_reason = executor_failure_shortcut.detect_catastrophic_failure(
            self, mut_output, run_log, trajectory
        )
        if catastrophic_reason is not None:
            executor_failure_shortcut.apply_failure_shortcut(
                result=result,
                catastrophic_reason=catastrophic_reason,
                diagnostics_enabled=diagnostics_enabled,
                runtime_artifacts=result.runtime_artifacts,
                trace=trace,
            )
            return result
        # --- End catastrophic failure short-circuit ---

        # Re-apply DDEV project name in case the MUT restored .ddev/config.yaml
        executor_failure_shortcut.reapply_ddev_project_name(workspace)

        # Stage: deterministic_checks  # noqa: ERA001
        current_stage = "deterministic_checks"
        scorer = _executor_globals().RuntimeScorer(
            workspace_path=workspace_dir,
            command_timeout_seconds=runtime_timeout_seconds,
            run_log_path=workspace_dir / "results" / "run" / "run.log",
        )
        check_results = scorer.run_deterministic_checks(checks_config)
        tool_allowlist_enforce = bool(runtime_config.get("runtime_tool_allowlist_enforce", False))
        tool_allowlist_check = RuntimeExecutionMixin._build_tool_allowlist_check(
            trajectory, rejected_tool_attempts, enforce=tool_allowlist_enforce
        )
        if tool_allowlist_check is not None:
            check_results.append(tool_allowlist_check)
        result.runtime_artifacts.update(self._extract_validation_artifacts(check_results))
        checks_payload = executor_stages.build_checks_payload(check_results)
        result.runtime_artifacts["checks.json"] = {"deterministic": checks_payload}

        executor_stages.stage_deterministic_checks(
            trace=trace,
            check_results=check_results,
        )

        # Stage: judge_scoring  # noqa: ERA001
        current_stage = "judge_scoring"
        judge_score, runtime_judge_output = executor_stages.run_judge_scoring(
            test_case=test_case,
            user_input=user_input,
            mut_output=mut_output,
            judge_runner=self.judge_runner,
            judge_system_prompt=self.judge_system_prompt,
            runtime_artifacts=result.runtime_artifacts,
            runtime_config=runtime_config,
        )

        hybrid_score = scorer.compute_hybrid_score(
            check_results=check_results,
            judge_score=judge_score,
            scoring_config=test_case.raw.get("scoring", {}),
        )

        result.judge_output = {
            "deterministic_score": hybrid_score.deterministic_score,
            "judge_score": hybrid_score.judge_score,
            "hybrid_score": hybrid_score.final_score,
            "final_score": hybrid_score.final_score,
            "deterministic_gate_passed": not any(c.is_critical and not c.passed for c in check_results),
            "checks": checks_payload,
            "runtime_judge": runtime_judge_output,
        }
        result.passed = hybrid_score.passed

        executor_stages.stage_judge_scoring(trace=trace, hybrid_score=hybrid_score)

        # Stage: artifact_finalization  # noqa: ERA001
        current_stage = "artifact_finalization"
        executor_stages.stage_artifact_finalization(
            trace=trace,
            result=result,
            check_results=check_results,
            diagnostics_enabled=diagnostics_enabled,
            runtime_artifacts=result.runtime_artifacts,
        )

    except Exception as exc:
        executor_stages.handle_exception(
            trace=trace,
            current_stage=current_stage,
            result=result,
            workspace=workspace,
            runtime_timeout_seconds=runtime_timeout_seconds,
            keep_workspace=keep_workspace,
            use_runtime_workspace=use_runtime_workspace,
            diagnostics_enabled=diagnostics_enabled,
            exc=exc,
        )

    finally:
        executor_cleanup.stage_cleanup(trace)
        executor_cleanup.cleanup_workspace(
            workspace=workspace,
            use_runtime_workspace=use_runtime_workspace,
            workspace_dir=workspace_dir,
            runtime_timeout_seconds=runtime_timeout_seconds,
            keep_workspace=keep_workspace,
        )
        executor_cleanup.finalize_and_attach_trace(
            trace=trace,
            result=result,
            diagnostics_enabled=diagnostics_enabled,
        )

    return result


# Attach to RuntimeExecutionMixin.
setattr(RuntimeExecutionMixin, "execute_runtime_test", execute_runtime_test)
