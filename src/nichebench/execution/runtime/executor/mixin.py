"""RuntimeExecutionMixin public facade.

Runtime-task execution mixin; inherits from ``CageExecutionMixin``.
Provides all supporting helpers for the runtime task lifecycle: preflight,
workspace bootstrap, task injection, cage/container execution, deterministic
checks, hybrid scoring, and artifact persistence.

This module owns:
    - All RuntimeExecutionMixin helper methods (prefixed with _)
    - Static/tool methods that delegate to runtime submodules
    - _executor_globals() patch-point for legacy access

This module does NOT own:
    - execute_runtime_test orchestration (see flow.py)
    - Stage-by-stage helpers (see stages.py)
    - Review nudge second-pass logic (see review_nudge.py)
    - Catastrophic failure short-circuit (see failure_shortcut.py)
    - Cleanup handling (see cleanup.py)

Usage:
    RuntimeExecutionMixin is mixed into TestExecutor (via CageExecutionMixin)
    and should not be instantiated directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.runtime import artifacts as runtime_artifacts
from nichebench.execution.runtime import checks as runtime_checks
from nichebench.execution.runtime import hints as runtime_hints
from nichebench.execution.runtime import image as runtime_image
from nichebench.execution.runtime import metadata as runtime_metadata
from nichebench.execution.runtime import opencode_config as runtime_opencode_config
from nichebench.execution.runtime import preflight as runtime_preflight
from nichebench.execution.runtime import trajectory as runtime_trajectory
from nichebench.execution.runtime.cage import CageExecutionMixin
from nichebench.execution.runtime.scoring import CheckResult
from nichebench.execution.runtime.wrappers import write_cage_git_wrapper

_OPENCODE_NATIVE_PROVIDERS = runtime_opencode_config.OPENCODE_NATIVE_PROVIDERS


def _executor_globals() -> Any:
    """Return orchestrator module so legacy patch points stay effective."""
    from nichebench.execution import orchestrator

    return orchestrator


class RuntimeExecutionMixin(CageExecutionMixin):
    """Runtime-task execution mixin; inherits from ``CageExecutionMixin``.

    Provides ``execute_runtime_test`` and all supporting helpers for the
    runtime task lifecycle: preflight, workspace bootstrap, task injection,
    cage/container execution, deterministic checks, hybrid scoring, and
    artifact persistence.

    Attributes:
        framework: Framework name (e.g., ``"drupal"``).
        category: Always ``"runtime"`` for this mixin.
        evaluation_config: Merged evaluation configuration dict.
        mut_runner: MUT runner instance.
        judge_runner: Judge runner instance.
        mut_model_str: MUT model string (e.g., ``"groq/llama-3.3-70b-versatile"``).
        judge_model_str: Judge model string.
        judge_system_prompt: Optional judge system prompt text.
        results_outdir: Root directory for result artifacts (set by
            ``setup_results_directory``).
        _cli_model_override: Raw ``--model`` CLI argument, if explicitly passed.
    """

    framework: str
    category: str
    evaluation_config: Dict[str, Any]
    mut_runner: Any
    judge_runner: Any
    mut_model_str: str
    judge_model_str: str
    judge_system_prompt: Optional[str]
    results_outdir: Optional[Path]
    _cli_model_override: Optional[str]

    def _run_runtime_preflight_host(
        self,
        runtime_config: Dict[str, Any],
        runtime_mode: str,
    ) -> None:
        """Run host-side preflight checks for runtime execution.

        Args:
            runtime_config: Runtime configuration dict
            runtime_mode: Effective runtime mode (cage/host)

        Raises:
            ValidationError: If preflight checks fail
        """
        runtime_preflight.run_runtime_preflight_host(
            runtime_config,
            runtime_mode,
            _executor_globals().subprocess,
            _executor_globals().ValidationError,
        )

    def _run_runtime_preflight_workspace(
        self,
        workspace_path: Path,
        runtime_mode: str,
    ) -> None:
        """Run workspace-side runtime preflight checks.

        Exists as a distinct hook for runtime/cage test coverage.
        """
        del runtime_mode
        script_path = Path(__file__).resolve().parents[5] / "scripts" / "runtime_smoke.py"
        runtime_preflight.run_runtime_preflight_workspace(
            workspace_path,
            self.evaluation_config,
            _executor_globals().subprocess,
            _executor_globals().sys.executable,
            script_path,
            _executor_globals().ValidationError,
        )

    def _inject_task_markdown(self, workspace_path: Path, test_case: TestCaseSpec) -> None:
        """Write ``TASK.md`` (from ``task_markdown`` field) into the workspace root.

        ``TASK.md`` is the canonical task specification injected as agent instructions
        at runtime; it takes precedence over the prompt/context fields when present.
        """
        task_markdown = str(test_case.raw.get("task_markdown", "")).strip()
        if not task_markdown:
            return
        (workspace_path / "TASK.md").write_text(task_markdown, encoding="utf-8")

    def _inject_runtime_hints(self, workspace_path: Path, test_case: TestCaseSpec) -> Optional[Path]:
        """Copy optional runtime hints to ``HINTS.md`` for diagnostic hinted runs.

        Hints are emitted when the manifest requests ``runtime_enable_hints`` and a
        ``hints_file`` or inline ``hints`` block is present. Returns the path to the
        written hints file, or None if no hints were injected.
        """
        return runtime_hints.inject_runtime_hints(
            workspace_path,
            test_case,
            self.evaluation_config,
            _executor_globals().ValidationError,
        )

    def _resolve_runtime_hints_file(self, test_case: TestCaseSpec) -> Optional[Path]:
        """Resolve the path to an optional HINTS.md file for hinted diagnostic runs."""
        return runtime_hints.resolve_runtime_hints_file(test_case, self.evaluation_config)

    def _load_runtime_checks(self, test_case: TestCaseSpec) -> List[Dict[str, Any]]:
        """Load and normalize runtime checks from the test case manifest.

        Checks are the deterministic validation layer (file existence, grep, drush
        commands, static analysis). Normalization applies ``RuntimeScorer.normalize_checks``.
        """
        return runtime_checks.load_runtime_checks(test_case, _executor_globals().RuntimeScorer.normalize_checks)

    @staticmethod
    def _looks_like_shell_command(value: str) -> bool:
        """Return True if ``value`` contains whitespace, suggesting a shell command."""
        return any(ch.isspace() for ch in value)

    @staticmethod
    def _resolve_runtime_checks_file(test_case: TestCaseSpec) -> Optional[Path]:
        """Resolve the path to the runtime checks manifest for the test case."""
        return runtime_checks.resolve_runtime_checks_file(test_case)

    @staticmethod
    def _load_runtime_checks_by_id(checks_path: Path) -> Dict[str, Dict[str, Any]]:
        """Load runtime checks indexed by check ID from a checks manifest file."""
        return runtime_checks.load_runtime_checks_by_id(checks_path)

    def _build_runtime_metadata(
        self,
        test_case: TestCaseSpec,
        profile: Any,
        runtime_mode: str,
        runtime_config: Dict[str, Any],
        workspace: Any,
        island_topology: Optional[Dict[str, Any]] = None,
        effective_image: Optional[str] = None,
        retry_info: Optional[Dict[str, Any]] = None,
        review_pass_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build runtime metadata dict.

        Args:
            test_case: Test case specification
            profile: Profile object
            runtime_mode: Runtime mode (cage/host)
            runtime_config: Runtime configuration
            workspace: Workspace object
            island_topology: Optional island topology mapping
            effective_image: Optional effective image (after DDEV resolution)
            retry_info: Optional retry info dict
            review_pass_info: Optional review pass info dict for two-pass nudge flow

        Returns:
            Metadata dict with runtime information
        """
        return runtime_metadata.build_runtime_metadata(
            test_case=test_case,
            profile=profile,
            runtime_mode=runtime_mode,
            runtime_config=runtime_config,
            workspace=workspace,
            mut_model_config=self.mut_runner.model_config,
            cli_model_override=self._cli_model_override,
            compute_opencode_model_binding=self._compute_opencode_model_binding,
            island_topology=island_topology,
            effective_image=effective_image,
            retry_info=retry_info,
            review_pass_info=review_pass_info,
        )

    def _resolve_effective_cage_image(
        self,
        runtime_config: Dict[str, Any],
    ) -> str:
        """Resolve effective cage image, handling DDEV capability checks and auto-build.

        Args:
            runtime_config: Runtime configuration dict

        Returns:
            Effective image tag to use

        Raises:
            ValidationError: If image resolution fails
        """
        return runtime_image.resolve_effective_cage_image(
            runtime_config,
            self._probe_image_for_ddev,
            self._build_ddev_image,
            _executor_globals().ValidationError,
        )

    def _probe_image_for_ddev(self, image: str) -> bool:
        """Probe image for ddev/docker/git and `ddev drush` command availability.

        Args:
            image: Image tag to probe

        Returns:
            True if required tooling exists, False otherwise
        """
        return runtime_image.probe_image_for_ddev(image, _executor_globals().subprocess)

    def _build_ddev_image(self, base_image: str, ddev_image: str) -> None:
        """Build DDEV-capable derived image.

        Args:
            base_image: Base image tag
            ddev_image: Derived image tag to build

        Raises:
            ValidationError: If build fails
        """
        dockerfile_path = Path(__file__).resolve().parents[5] / "docker" / "opencode-ddev" / "Dockerfile"
        runtime_image.build_ddev_image(
            base_image,
            ddev_image,
            dockerfile_path,
            _executor_globals().subprocess,
            _executor_globals().ValidationError,
        )

    @staticmethod
    def _dump_opencode_session_state(db_path: Path) -> Optional[Dict[str, Any]]:
        """Best-effort raw OpenCode session dump for timeout/catastrophic-failure forensics.

        Dumps the raw SQLite session state so analysts can reconstruct the agent's
        last known message tree when the run log is incomplete.
        """
        return runtime_trajectory.dump_opencode_session_state(db_path)

    @staticmethod
    def _resolve_watchdog_marker(
        has_stop: bool,
        idle_secs: float,
        stop_idle_seconds: float,
        inactivity_seconds: float,
    ) -> Optional[str]:
        """Compute the watchdog trigger message from idle/threshold state.

        Returns a human-readable trigger reason when the agent has been idle past
        ``stop_idle_seconds`` or inactive past ``inactivity_seconds``, otherwise None.
        """
        return runtime_trajectory.resolve_watchdog_marker(has_stop, idle_secs, stop_idle_seconds, inactivity_seconds)

    @staticmethod
    def _poll_opencode_db(db_path: Path) -> Tuple[Optional[str], bool]:
        """Poll the OpenCode SQLite DB for watchdog conditions.

        Returns (marker, has_stop) where ``marker`` signals a state transition
        and ``has_stop`` indicates whether the agent sent a ``stop`` tool call.
        """
        return runtime_trajectory.poll_opencode_db(db_path)

    @staticmethod
    def _write_cage_opencode_json(
        workspace_host_path: Path,
        opencode_provider: str,
        opencode_model_id: str,
        api_base: Optional[str] = None,
        runtime_config: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Write ``opencode.json`` into the cage workspace root for the MUT's agent."""
        return runtime_opencode_config.write_cage_opencode_json(
            workspace_host_path, opencode_provider, opencode_model_id, api_base, runtime_config
        )

    @staticmethod
    def _write_cage_git_wrapper(bin_host: Path) -> Path:
        """Write cage-local ``git`` wrapper scripts that block unsafe MUT operations.

        The wrapper intercepts ``push``, ``force-push``, and ``rebase`` commands and
        replaces them with no-ops to prevent accidental destructive operations inside
        the cage workspace.
        """
        return write_cage_git_wrapper(bin_host)

    @staticmethod
    def _derive_cage_npm_provider_key(opencode_provider: str, runtime_config: Dict[str, Any]) -> str:
        """Derive the provider key for npm-based cage runs (used in opencode.json and --model).

        When ``runtime_opencode_provider_name`` is set, that value is used directly;
        otherwise the original provider name is sanitized to a key-safe string.
        """
        return runtime_opencode_config.derive_cage_npm_provider_key(opencode_provider, runtime_config)

    @staticmethod
    def _load_review_nudge() -> str:
        """Load the review-nudge prompt text from the executor prompts bundle.

        The nudge is delivered as a fresh user message in the second MUT pass
        (two-pass quality flow) when the first pass produced meaningful work.
        """
        return runtime_opencode_config.load_review_nudge()

    @staticmethod
    def _compute_opencode_model_binding(
        mut_provider: str,
        mut_model: str,
        runtime_config: Dict[str, Any],
        cli_model_override: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Compute OpenCode model binding from MUT provider/model.

        Args:
            mut_provider: MUT provider (e.g., "groq", "openai")
            mut_model: MUT model name (e.g., "gemma2-9b-it", "openai/gpt-oss-120b")
            runtime_config: Runtime configuration dict
            cli_model_override: If set, the raw --model CLI arg was provided
                explicitly and takes precedence over ``runtime_opencode_model``
                in the config.

        Returns:
            Tuple of (provider, model_id) for OpenCode binding
        """
        return runtime_opencode_config.compute_opencode_model_binding(
            mut_provider, mut_model, runtime_config, cli_model_override
        )

    @staticmethod
    def _get_provider_api_keys(provider: str) -> Dict[str, str]:
        """Return API key env vars for ``provider`` that are set on the host.

        Only keys that actually exist in the host environment are returned,
        allowing the cage to inherit credentials without hardcoding them.
        """
        return runtime_opencode_config.get_provider_api_keys(provider)

    @staticmethod
    def _read_workspace_system_prompt(workspace_path: Path) -> Optional[str]:
        """Extract ``mode.build.prompt`` from the workspace ``opencode.json`` (if present)."""
        return runtime_opencode_config.read_workspace_system_prompt(workspace_path)

    @staticmethod
    def _opencode_sessions_dir(xdg_data_home: Optional[Path] = None) -> Optional[Path]:
        """Locate the OpenCode sessions directory (``$XDG_DATA_HOME/opencode``)."""
        return runtime_trajectory.opencode_sessions_dir(xdg_data_home)

    @staticmethod
    def _snapshot_session_ids(sessions_dir: Optional[Path]) -> set[str]:
        """Return the set of session directory names (IDs) currently in the sessions dir."""
        return runtime_trajectory.snapshot_session_ids(sessions_dir)

    @staticmethod
    def _pick_newest_session(sessions_dir: Path, session_ids: set[str]) -> Optional[Path]:
        """Return the newest session directory by modification time from the given set."""
        return runtime_trajectory.pick_newest_session(sessions_dir, session_ids)

    @staticmethod
    def _pick_session_by_mtime(sessions_dir: Path, window_start: datetime, window_end: datetime) -> Optional[Path]:
        """Return the first session directory modified within the given time window."""
        return runtime_trajectory.pick_session_by_mtime(sessions_dir, window_start, window_end)

    @staticmethod
    def _normalise_message(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a message row from the OpenCode SQLite store to the canonical format."""
        return runtime_trajectory.normalise_message(raw)

    def _build_trajectory(
        self,
        session_dir: Path,
        test_case_id: str,
        model_str: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]:
        """Assemble the full trajectory (message list + tool calls) from a completed session dir."""
        return runtime_trajectory.build_trajectory(session_dir, test_case_id, model_str, start_time, end_time)

    def _save_runtime_artifacts(self, result: Any) -> None:
        """Persist runtime artifacts (run.log, trajectory, checks, etc.) to the results dir.

        Delegates to ``runtime_artifacts.save_runtime_artifacts`` after applying
        payload redaction for secret-like values.
        """
        if not hasattr(self, "results_outdir") or not self.results_outdir:
            return
        runtime_artifacts.save_runtime_artifacts(
            result=result,
            results_outdir=self.results_outdir,
            evaluation_config=self.evaluation_config,
            redact_func=self._redact_artifact_payload,
        )

    @staticmethod
    def _extract_validation_artifacts(check_results: List[CheckResult]) -> Dict[str, str]:
        """Extract focused validation artifacts (PHPStan, PHPCS, watchdog) from check results.

        Returns a dict mapping artifact filename to its content for checks that
        produce auxiliary output files.
        """
        return runtime_artifacts.extract_validation_artifacts(check_results)

    @staticmethod
    def _redact_artifact_payload(payload: Any) -> Any:
        """Walk an artifact payload and replace secret-like values (API keys, tokens)."""
        return runtime_artifacts.redact_artifact_payload(payload)

    @staticmethod
    def _extract_trajectory_tool_names(trajectory: Dict[str, Any]) -> set[str]:
        """Collect all tool names referenced in trajectory messages (lowercased, deduplicated)."""
        return runtime_artifacts.extract_trajectory_tool_names(trajectory)

    @staticmethod
    def _parse_rejected_tool_attempts(run_log: str) -> List[Dict[str, str]]:
        """Extract rejected tool attempt records from the run.log (stderr output)."""
        return runtime_artifacts.parse_rejected_tool_attempts(run_log)

    @classmethod
    def _build_tool_allowlist_check(
        cls,
        trajectory: Optional[Dict[str, Any]],
        rejected_tool_attempts: Optional[List[Dict[str, str]]] = None,
        enforce: bool = False,
    ) -> Optional[CheckResult]:
        """Build deterministic tool allowlist check from trajectory and rejected attempts."""
        if not trajectory and not rejected_tool_attempts:
            return None

        used_tools = cls._extract_trajectory_tool_names(trajectory) if trajectory else set()
        if rejected_tool_attempts:
            rejected_tool_names = {
                attempt["tool_name"].strip().lower() for attempt in rejected_tool_attempts if attempt.get("tool_name")
            }
            used_tools = used_tools | rejected_tool_names

        allowed_tools = {"bash", "read", "write", "edit"}
        disallowed_tools = sorted(tool for tool in used_tools if tool not in allowed_tools)
        passed = not disallowed_tools if enforce else True

        return _executor_globals().CheckResult(
            name="tool_allowlist_guard",
            type="tool_allowlist",
            passed=passed,
            message=(
                "Only allowlisted tools were used"
                if not disallowed_tools
                else f"Disallowed tools detected: {', '.join(disallowed_tools)}"
            ),
            is_critical=enforce,
            details={
                "allowed_tools": sorted(allowed_tools),
                "used_tools": sorted(used_tools),
                "disallowed_tools": disallowed_tools,
                "rejected_tool_attempts": rejected_tool_attempts or [],
                "enforce_mode": enforce,
            },
        )

    @staticmethod
    def _detect_catastrophic_failure(
        mut_output: str,
        run_log: str,
        trajectory: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Detect catastrophic agent failures (empty output, timeout before tool use).

        Returns a reason string when the agent crashed or produced no meaningful
        work before deterministic checks would give misleading signal.
        """
        return runtime_artifacts.detect_catastrophic_failure(run_log, trajectory, mut_output)

    @staticmethod
    def _build_trajectory_from_sqlite(
        db_path: Path,
        test_case_id: str,
        model_str: str,
        start_time: datetime,
        end_time: datetime,
        system_prompt: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Reconstruct the trajectory dict by querying the OpenCode SQLite session DB."""
        return runtime_trajectory.build_trajectory_from_sqlite(
            db_path, test_case_id, model_str, start_time, end_time, system_prompt
        )
