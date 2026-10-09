"""CageExecutionMixin and container lifecycle orchestration.

**Ownership**: This module is owned by the cage package and is the public
interface for cage-mode container execution. All other cage submodules
(islands, docker_args, process_io, watchdog, retry, artifacts) are
implementation details owned by this mixin.

**Container safety constraints**:
- Docker socket is mounted read-write because DDEV inside the cage needs to
  invoke ``docker`` to build/start the project's containers.  This is an
  intentional trust boundary: a model-controlled cage process can ask the
  host Docker daemon to start containers and mount host paths.  ``--cap-drop=ALL``
  and the non-root user do not constrain Docker daemon access.  Operators must
  understand this and run the cage only in trusted, isolated environments.
- Container runs with dropped capabilities and no-new-privileges.
- All cage state is run-scoped (temp directories, not host user state).
- Unsafe git operations are blocked by the cage git wrapper (see ``_write_cage_git_wrapper``).
- No secrets are hardcoded; API keys are injected from host env at runtime.
"""

from __future__ import annotations

import codecs
import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.runtime.cage import retry as cage_retry
from nichebench.execution.runtime.capture import CaptureAttempt, CaptureSession, EventJournal
from nichebench.execution.runtime.capture.errors import CaptureError
from nichebench.execution.runtime.capture.proxy import DEFAULT_UPSTREAM as CAPTURE_MINIMAX_UPSTREAM


def _executor_globals() -> Any:
    """Return the orchestrator module for backward-compatibility patch points.

    Allows runtime submodules to access re-exported symbols (``subprocess``,
    ``ValidationError``, ``RuntimeScorer``, etc.) that the test-harness
    monkey-patches at load time without touching the actual runtime code.
    """
    from nichebench.execution import orchestrator

    return orchestrator


def resolve_capture_route(runtime_config: Dict[str, Any]) -> Optional[str]:
    """Resolve the capture route identifier for a runtime configuration.

    Only the pinned MiniMax M3 route is implemented. Any other provider or
    endpoint resolves to ``None`` so :func:`CaptureSettings.assert_route_supported`
    fails loudly instead of producing a partial capture.
    """
    api_base = str(runtime_config.get("runtime_opencode_api_base", "") or "").rstrip("/")
    provider = str(runtime_config.get("runtime_opencode_provider_name", "") or "").lower()
    model = str(runtime_config.get("runtime_opencode_model", "") or "").lower()
    if api_base == CAPTURE_MINIMAX_UPSTREAM and provider in ("", "minimax") and "minimax" in (model or provider):
        return "minimax_m3"
    if api_base == CAPTURE_MINIMAX_UPSTREAM and provider in ("", "minimax"):
        return "minimax_m3"
    return None


# Teardown budget for ``docker rm -f``. In the real runtime the child *is* the
# docker CLI, so signalling it already reaps the container; this call is a
# belt-and-braces cleanup for the case where the CLI died first. Kept short
# because the run is over either way.
CONTAINER_REAP_TIMEOUT_SECONDS = 5.0

# Reader threads normally see EOF as soon as the child's process group is
# signalled; the cap only guards a wedged pipe.
STREAM_DRAIN_TIMEOUT_SECONDS = 5.0


def _resolve_task_input(
    test_case: TestCaseSpec,
    workspace_host_path: Path,
    task_input_override: Optional[str],
) -> str:
    """Resolve the task input: explicit override > TASK.md > prompt/context."""
    if task_input_override is not None:
        return task_input_override
    prompt = getattr(test_case, "prompt", "") or test_case.raw.get("prompt", "") or ""
    context = getattr(test_case, "context", "") or test_case.raw.get("context", "") or ""
    task_input = prompt if not context else f"{prompt}\n\nContext:\n{context}"
    try:
        task_markdown = (workspace_host_path / "TASK.md").read_text(encoding="utf-8").strip()
        if task_markdown:
            task_input = task_markdown
    except OSError:
        pass
    return task_input


def _upstream_auth(api_keys: Dict[str, str]) -> Optional[str]:
    """Pick the provider credential the proxy must present upstream.

    Memory only: the value is handed to the proxy, never journalled. The proxy
    substitutes it for the capability token on forwarded requests and scrubs it
    from stored bodies.
    """
    for key in sorted(api_keys):
        if key.endswith("_API_KEY") and api_keys[key]:
            return api_keys[key]
    return None


def _known_provider_credentials(runtime_config: Dict[str, Any]) -> List[str]:
    """Collect provider credential values held in the host environment.

    These are compared against captured payloads for exact scrubbing. The values
    themselves are never written to the archive; only the fact that a scrub
    happened is recorded.
    """
    del runtime_config
    values: List[str] = []
    for env_name in (
        "MINIMAX_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "MISTRAL_API_KEY",
        "TOGETHERAI_API_KEY",
        "AZURE_API_KEY",
    ):
        value = os.environ.get(env_name)
        if value:
            values.append(value)
    return values


def _read_run_log_best_effort(workspace: Any, test_case_id: str) -> Optional[str]:
    """Read the run.log written by the first cage run, if available.

    The cage always writes ``run.log`` (or its partial/timeout/watchdog variant)
    to the output trace island before raising on a non-zero exit.  This helper
    attempts to recover that log so the retry decision can inspect it for
    ``invalid_request_error`` / rejected-tool patterns.

    Args:
        workspace: ``Workspace`` instance (or any object exposing
            ``run_artifacts_path``).
        test_case_id: Test case id; reserved for future per-id recovery.

    Returns:
        Combined run.log text, or ``None`` if no log file is readable.
    """
    _ = test_case_id  # reserved; kept for future per-id recovery
    rap = getattr(workspace, "run_artifacts_path", None)
    if not isinstance(rap, (str, Path)):
        return None
    log_path = Path(rap) / "run.log"
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class CageExecutionMixin:
    """Cage-mode container execution mixin.

    This mixin is mixed into ``RuntimeExecutionMixin`` (in ``executor.py``) to
    provide the ``_run_container_runtime_task_with_retry`` method and related
    container lifecycle helpers.  All delegation methods (``_poll_opencode_db``,
    ``_build_trajectory_from_sqlite``, etc.) are implemented in
    ``RuntimeExecutionMixin``, not here.
    """

    mut_runner: Any
    mut_model_str: str
    _cli_model_override: Optional[str]

    # ------------------------------------------------------------------
    # Stubs for methods implemented in RuntimeExecutionMixin (executor.py).
    #
    # These ``*args, **kwargs -> Any`` stubs with ``raise NotImplementedError``
    # satisfy mypy's type checking for call sites in this file (via MRO the
    # actual implementations in RuntimeExecutionMixin are called at runtime).
    # ------------------------------------------------------------------

    def _parse_rejected_tool_attempts(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    # Actual implementation (kept in the cage mixin rather than the stub block
    # because it needs only stdlib plus capture helpers, not executor state).
    def _allocate_capture_session(
        self,
        runtime_config: Dict[str, Any],
        test_case_id: str,
    ) -> Optional[CaptureSession]:
        """Allocate a run-local capture session, or ``None`` when disabled.

        The session root is allocated from ``executor.results_outdir`` — never
        from a mutable executor global — so parallel tasks stay isolated and the
        archive survives task cleanup.
        """
        if not runtime_config.get("runtime_capture_enable", False):
            return None

        from nichebench.execution.runtime.capture import CaptureRun, CaptureSettings

        settings = CaptureSettings.from_runtime_config(runtime_config, route=resolve_capture_route(runtime_config))
        settings.assert_route_supported()
        outdir = getattr(self, "results_outdir", None)
        if outdir is None:
            raise _executor_globals().ValidationError(
                "runtime capture requires results_outdir to be set (call setup_results_directory first)"
            )
        run = CaptureRun.allocate(Path(outdir), settings, test_case_id)
        return CaptureSession(run, known_credentials=_known_provider_credentials(runtime_config))

    def _compute_opencode_model_binding(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _derive_cage_npm_provider_key(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _get_provider_api_keys(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _write_cage_opencode_json(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _write_cage_git_wrapper(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _resolve_effective_cage_image(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _read_workspace_system_prompt(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _build_trajectory_from_sqlite(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _dump_opencode_session_state(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _poll_opencode_db(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _resolve_watchdog_marker(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _run_container_runtime_task_with_retry(
        self,
        test_case: TestCaseSpec,
        workspace: Any,
        agent_manifest: Dict[str, Any],
        runtime_config: Dict[str, Any],
        profile: Any,
        timeout_seconds: int,
        task_input_override: Optional[str] = None,
        capture: Optional[CaptureSession] = None,
        attempt_label: str = "initial",
    ) -> Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Run cage task with one-step auto-retry for invalid_request_error due to unknown tool.

        When the first run fails with a rejected tool attempt (e.g., 'exec' not in
        request.tools), retries once with an appended instruction to use 'bash' for
        shell commands and continue.

        The first run may raise ``RuntimeError`` (non-zero exit, timeout, watchdog).
        In that case the captured ``run.log`` is replayed as a synthetic failed-result
        tuple so the retry decision can still inspect it for ``invalid_request_error``
        patterns.  If no retry trigger is found, the original ``RuntimeError`` is
        re-raised so the executor can still classify the catastrophic failure.

        Args:
            capture: Optional capture session. When present, every attempt opens
                its own immutable journal directory; the session is finalised on
                every exit path (normal, non-zero, timeout, watchdog, interrupt,
                poll error) so nothing is lost before workspace cleanup.
            attempt_label: Capture label for this attempt. Retries and review
                passes supply their own labels.

        Returns:
            Tuple of (mut_output, user_input, run_log, island_topology, effective_image,
                     trajectory, retry_info)
            trajectory is None if capture fails (best-effort).
            retry_info is None if no retry occurred, otherwise {"attempted": True, "reason": str}
        """
        first_failed_exc: Optional[BaseException] = None
        first_run_result: Optional[Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]]]] = None
        try:
            first_run_result = self._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest=agent_manifest,
                runtime_config=runtime_config,
                profile=profile,
                timeout_seconds=timeout_seconds,
                task_input_override=task_input_override,
                capture=capture,
                attempt_label=attempt_label,
            )
        except RuntimeError as exc:
            first_failed_exc = exc

        if first_failed_exc is not None:
            # The first run wrote run.log to the output trace island; read it back so
            # the retry decision can inspect what happened.  If we cannot recover the
            # log, fall through to re-raise the original error.
            run_log_recovered = _read_run_log_best_effort(workspace, test_case.id)
            if run_log_recovered is None:
                raise first_failed_exc from first_failed_exc
            first_run_result = (
                "",  # mut_output: empty on failure
                "",  # user_input: empty on failure
                run_log_recovered,
                {},  # island_topology: unknown on failure
                "",  # effective_image: unknown on failure
                None,  # trajectory: best-effort, none
            )

        assert first_run_result is not None  # for mypy
        if capture is not None and capture.capture_stopped:
            raise RuntimeError("Runtime capture storage failed; retries disabled")

        max_retry_attempts = cage_retry.get_max_retry_attempts(runtime_config)

        final = cage_retry.execute_retry_loop(
            first_run_result=first_run_result,
            test_case=test_case,
            workspace=workspace,
            agent_manifest=agent_manifest,
            runtime_config=runtime_config,
            profile=profile,
            timeout_seconds=timeout_seconds,
            task_input_override=task_input_override,
            run_container_task_fn=self._run_container_runtime_task,
            parse_rejected_tool_attempts_fn=self._parse_rejected_tool_attempts,
            max_retry_attempts=max_retry_attempts,
            attempt_label=attempt_label,
            capture=capture,
        )

        # If the first run failed and no retry trigger was found in run.log, the
        # retry loop will have returned the synthetic failed-result tuple unchanged.
        # Re-raise the original error in that case so the executor still records a
        # catastrophic failure rather than silently treating it as a normal exit.
        if first_failed_exc is not None and final[-1] is None:
            raise first_failed_exc

        return final

    def _run_container_runtime_task(
        self,
        test_case: TestCaseSpec,
        workspace: Any,
        agent_manifest: Dict[str, Any],
        runtime_config: Dict[str, Any],
        profile: Any,
        timeout_seconds: int,
        task_input_override: Optional[str] = None,
        capture: Optional[CaptureSession] = None,
        attempt_label: str = "initial",
    ) -> Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]]]:
        """Cover setup failures as well as execution failures with teardown."""
        try:
            return self._execute_container_runtime_task(
                test_case,
                workspace,
                agent_manifest,
                runtime_config,
                profile,
                timeout_seconds,
                task_input_override,
                capture,
                attempt_label,
            )
        except BaseException as exc:
            if capture is not None:
                capture.mark_incomplete(f"attempt_failed:{type(exc).__name__}")
            raise
        finally:
            if capture is not None:
                attempt = capture.run.attempts[-1] if capture.run.attempts else None
                with contextlib.suppress(Exception):
                    capture.finalise(attempt)
                state_root = capture.context.get("cage_state_cleanup")
                if isinstance(state_root, Path):
                    shutil.rmtree(state_root, ignore_errors=True)

    def _execute_container_runtime_task(
        self,
        test_case: TestCaseSpec,
        workspace: Any,
        agent_manifest: Dict[str, Any],
        runtime_config: Dict[str, Any],
        profile,
        timeout_seconds: int,
        task_input_override: Optional[str] = None,
        capture: Optional[CaptureSession] = None,
        attempt_label: str = "initial",
    ) -> Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]]]:
        """Run OpenCode inside a dedicated container with Docker socket access.

        Spawns a cage container that:

        * Mounts the workspace at its real host path (required for DDEV bind mounts)
        * Exposes input, output/trace, and optional ops islands for I/O isolation
        * Writes ``opencode.json`` for the MUT's model binding
        * Installs a cage-local ``git`` wrapper that blocks unsafe operations
        * Polls the OpenCode SQLite DB for watchdog (idle/inactivity) termination
        * Captures best-effort trajectory from the session SQLite on exit

        Execution path
        --------------
        With watchdog disabled and capture disabled the historical
        ``subprocess.run`` path is used unchanged. In every other case — and
        always when capture is enabled — a single ``Popen`` + reader-thread path
        runs, so stdout/stderr can be spooled byte-for-byte with observation
        timestamps while the watchdog loop is active.

        Args:
            test_case: Test case specification.
            workspace: ``Workspace`` instance (runtime task branch checkout).
            agent_manifest: Agent configuration from the task manifest.
            runtime_config: Full runtime configuration dict.
            profile: Resolved profile object.
            timeout_seconds: Hard timeout for the container command.
            task_input_override: If set, overrides task input (used for the
                second pass in the two-pass review nudge flow).
            capture: Optional capture session for this attempt.
            attempt_label: Capture label for this attempt.

        Returns:
            Tuple of (mut_output, user_input, run_log, island_topology,
            effective_image, trajectory).  ``trajectory`` is ``None`` if capture
            fails (best-effort); ``run_log`` always contains stdout/stderr.
        """
        if isinstance(workspace, _executor_globals().Workspace):
            workspace_host_path = Path(workspace.path).resolve()
        else:
            workspace_host_path = Path(workspace.path).resolve()

        task_input = _resolve_task_input(test_case, workspace_host_path, task_input_override)

        # DDEV inside the cage talks to the host Docker daemon via the mounted
        # docker socket. Any bind mounts DDEV asks Docker to create must use a
        # path that exists on the host, not a cage-only alias like /workspace.
        # Mount the workspace at its real host path inside the cage so `ddev`
        # run by the MUT sees a Docker-valid project root.
        workspace_container_path = str(workspace_host_path)
        input_island_host = workspace_host_path
        input_island_container = "/nichebench/islands/input"

        env = {
            "NB_TASK_ID": test_case.id,
            "NB_TOOL_PROFILE": profile.name,
            "NB_MODEL_PROVIDER": str(self.mut_runner.model_config.get("provider", "")),
            "NB_MODEL_NAME": str(self.mut_runner.model_config.get("model", "")),
            "NB_RUNTIME_MODE": "cage",
            "USER": "opencode",
        }

        mut_provider = str(self.mut_runner.model_config.get("provider", "")).strip()
        mut_model_name = str(self.mut_runner.model_config.get("model", "")).strip()
        if not mut_provider or not mut_model_name:
            raise _executor_globals().ValidationError("Cage mode requires explicit MUT provider/model binding")

        # Compute OpenCode model binding with normalization
        opencode_provider, opencode_model_id = self._compute_opencode_model_binding(
            mut_provider,
            mut_model_name,
            runtime_config,
            cli_model_override=self._cli_model_override,
        )

        # Resolve the api_base early — needed for provider remapping below.
        cage_api_base_raw = runtime_config.get("runtime_opencode_api_base")
        cage_api_base: Optional[str] = None
        if cage_api_base_raw:
            cage_api_base = str(cage_api_base_raw).rstrip("/")
            if not cage_api_base.endswith("/v1"):
                cage_api_base += "/v1"

        # When api_base is configured, use an npm-based @ai-sdk/openai-compatible
        # provider with a derived key instead of remapping to the built-in "openai".
        # Without api_base, non-native providers still fall back to "openai".
        if cage_api_base:
            npm_key = self._derive_cage_npm_provider_key(opencode_provider, runtime_config)
            opencode_model_binding = f"{npm_key}/{opencode_model_id}"
        else:
            if opencode_provider not in _executor_globals()._OPENCODE_NATIVE_PROVIDERS:
                opencode_provider = "openai"
            opencode_model_binding = f"{opencode_provider}/{opencode_model_id}"

        # Get provider API keys from host environment
        api_keys = self._get_provider_api_keys(opencode_provider)

        # --- Capture allocation happens BEFORE the cage is launched so a hard
        # failure still leaves an archive describing the attempt. ---
        capture_attempt: Optional[CaptureAttempt] = None
        capture_journal: Optional[EventJournal] = None
        capture_spool_stdout = None
        capture_spool_stderr = None
        proxy_handle = None
        capture_proxy_base: Optional[str] = None
        if capture is not None:
            capture.known_credentials.extend(v for v in api_keys.values() if v not in capture.known_credentials)
            attempt = capture.open_attempt(attempt_label)
            journal = capture.journal_for(attempt)
            capture_attempt, capture_journal = attempt, journal
            capture_spool_stdout = capture.spool(attempt, "stdout")
            capture_spool_stderr = capture.spool(attempt, "stderr")
            capture.set_context(attempt_label=attempt_label, attempt_index=attempt.index)

        effective_api_base = cage_api_base
        if capture is not None and capture_journal is not None:
            if not cage_api_base:
                capture.mark_incomplete("capture_requires_opencode_api_base")
            else:
                try:
                    proxy_handle = capture.start_proxy(
                        capture_journal,
                        cage_api_base,
                        upstream_auth=_upstream_auth(api_keys),
                    )
                    capture_proxy_base = proxy_handle.base_url
                    if proxy_handle.token:
                        env["OPENAI_API_KEY"] = proxy_handle.token
                        api_keys = dict(api_keys)
                        for key in list(api_keys):
                            if key.endswith("_API_KEY"):
                                api_keys[key] = proxy_handle.token
                    effective_api_base = capture_proxy_base
                except Exception as exc:  # noqa: BLE001 - capture failure must be explicit
                    capture.mark_incomplete(f"proxy_start_failed:{type(exc).__name__}")
                    raise

        opencode_json_path = self._write_cage_opencode_json(
            workspace_host_path=workspace_host_path,
            opencode_provider=opencode_provider,
            opencode_model_id=opencode_model_id,
            api_base=effective_api_base,
            runtime_config=runtime_config,
        )
        if capture is not None and capture_journal is not None and capture_attempt is not None:
            capture_journal.append(
                {
                    "event": "effective_config_written",
                    "path": str(opencode_json_path),
                    "provider_key": opencode_provider,
                    "model_id": opencode_model_id,
                    "base_url": effective_api_base,
                    "base_url_is_capture_proxy": capture_proxy_base is not None,
                },
                boundary="agent_timeline",
                source="config",
            )
            capture.capture_effective_config(capture_attempt, opencode_json_path)

        # Run-scoped OpenCode state roots prevent any inheritance from host user
        # state and stay outside of task workspace to avoid diff pollution.
        state_root_tmp = tempfile.mkdtemp(prefix="nichebench-cage-state-")
        state_root = Path(state_root_tmp)
        if capture is not None:
            capture.set_context(cage_state_cleanup=state_root)
        home_host = state_root / "home"
        xdg_config_host = state_root / "xdg-config"
        xdg_data_host = state_root / "xdg-data"
        xdg_state_host = state_root / "xdg-state"
        xdg_cache_host = state_root / "xdg-cache"
        bin_host = state_root / "bin"
        for path in (home_host, xdg_config_host, xdg_data_host, xdg_state_host, xdg_cache_host, bin_host):
            path.mkdir(parents=True, exist_ok=True)
        self._write_cage_git_wrapper(bin_host)
        if capture is not None:
            capture.set_context(cage_xdg_data_host=xdg_data_host)

        _raw_rap = getattr(workspace, "run_artifacts_path", None)
        output_island_host = (
            Path(_raw_rap).resolve()
            if isinstance(_raw_rap, (str, Path))
            else (workspace_host_path / "results" / "run").resolve()
        )
        output_island_host.mkdir(parents=True, exist_ok=True)
        output_trace_island_container = "/nichebench/islands/output-trace"
        trace_host_path = output_island_host / "trace"
        trace_host_path.mkdir(parents=True, exist_ok=True)
        trace_container_path = f"{output_trace_island_container}/trace"

        island_topology: Dict[str, Any] = {
            "workspace": {
                "host_path": str(workspace_host_path),
                "container_path": workspace_container_path,
            },
            "input_island": {
                "host_path": str(input_island_host),
                "container_path": input_island_container,
            },
            "output_trace_island": {
                "host_path": str(output_island_host),
                "container_path": output_trace_island_container,
                "trace_host_path": str(trace_host_path),
                "trace_container_path": trace_container_path,
            },
        }

        env["NB_ISLAND_INPUT"] = input_island_container
        env["NB_ISLAND_OUTPUT_TRACE"] = output_trace_island_container
        env["NB_ISLAND_OUTPUT"] = output_trace_island_container
        env["NB_ISLAND_TRACE"] = trace_container_path

        ops_island_host_path = runtime_config.get("runtime_ops_island_host_path")
        enable_ops_island = bool(runtime_config.get("runtime_enable_ops_island", False) or ops_island_host_path)
        if enable_ops_island:
            ops_island_host = Path(str(ops_island_host_path)) if ops_island_host_path else (state_root / "ops-island")
            ops_island_host.mkdir(parents=True, exist_ok=True)
            ops_island_container = "/nichebench/islands/ops"
            island_topology["ops_island"] = {
                "host_path": str(ops_island_host),
                "container_path": ops_island_container,
            }
            env["NB_ISLAND_OPS"] = ops_island_container

        container_state_root = "/nichebench/state"
        env["HOME"] = f"{container_state_root}/home"
        env["XDG_CONFIG_HOME"] = f"{container_state_root}/xdg-config"
        env["XDG_DATA_HOME"] = f"{container_state_root}/xdg-data"
        env["XDG_STATE_HOME"] = f"{container_state_root}/xdg-state"
        env["XDG_CACHE_HOME"] = f"{container_state_root}/xdg-cache"
        env["PATH"] = f"{container_state_root}/bin:" "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

        # When api_base is configured, pass it to OpenCode inside the cage
        if cage_api_base:
            env["OPENAI_BASE_URL"] = effective_api_base
            env.setdefault("OPENAI_API_KEY", "dummy")

        # Resolve effective cage image (handles DDEV capability checks and auto-build)
        image = self._resolve_effective_cage_image(runtime_config)
        if capture is not None and capture_attempt is not None:
            capture.capture_identity(capture_attempt, image)
            probe = capture.probe_health(image)
            if probe.get("probed") and not probe.get("healthy"):
                # A connectivity problem is worth surfacing before the agent
                # burns a run, but it is not fatal: the container may still
                # resolve the gateway differently than the probe container.
                capture.mark_incomplete("container_health_probe_failed")
                raise RuntimeError("Capture proxy is unreachable from the cage image")
        runtime_user = str(runtime_config.get("runtime_container_user", "1000:1000"))
        read_only = bool(runtime_config.get("runtime_container_read_only", False))
        container_name = f"nichebench-{test_case.id}-{state_root.name}".replace("_", "-")
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
        if capture is not None:
            from nichebench.execution.runtime.capture.proxy import build_gateway_docker_args

            command.extend(build_gateway_docker_args())
        # Add docker socket group access for non-root user (best effort)
        try:
            docker_socket_gid = os.stat("/var/run/docker.sock").st_gid
            command.extend(["--group-add", str(docker_socket_gid)])
        except Exception:
            pass
        command.extend(
            [
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
        )
        if "ops_island" in island_topology:
            command.extend(
                [
                    "-v",
                    (
                        f"{island_topology['ops_island']['host_path']}"
                        f":{island_topology['ops_island']['container_path']}"
                    ),
                ]
            )
        if read_only:
            command.extend(["--read-only", "--tmpfs", "/tmp", "--tmpfs", "/run"])
        for key, value in env.items():
            command.extend(["-e", f"{key}={value}"])
        # Add provider API keys from host environment
        for key, value in api_keys.items():
            command.extend(["-e", f"{key}={value}"])
        command.extend(["--entrypoint", "opencode", image, "run", "--pure"])
        if capture is not None:
            # Structured JSON event stream plus reasoning parts. The flags are
            # appended only when capture is enabled so the non-capture command
            # is byte-identical to the historical one.
            command.extend(["--format", "json", "--thinking"])
        command.extend(
            [
                "--dangerously-skip-permissions",
                "--model",
                opencode_model_binding,
                task_input,
            ]
        )

        db_path = xdg_data_host / "opencode" / "opencode.db"
        watchdog_enable = bool(runtime_config.get("runtime_watchdog_enable", True))
        use_popen_path = watchdog_enable or capture is not None
        termination_reason = "normal_exit"

        def _force_remove_cage_container() -> None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=CONTAINER_REAP_TIMEOUT_SECONDS,
                )

        def _finalize_capture(reason: str) -> None:
            if capture is None or capture_attempt is None:
                return
            # Never mask the real failure: a capture problem must not hide the
            # reason the run ended.
            with contextlib.suppress(Exception):
                capture.finalise(capture_attempt, reason)

        def _salvage_session_state(run_end: datetime) -> None:
            """Best-effort non-capture salvage on a failing subprocess.run exit."""
            try:
                raw_dump = self._dump_opencode_session_state(db_path)
                if raw_dump:
                    (output_island_host / "opencode_session_dump.json").write_text(
                        json.dumps(raw_dump, indent=2),
                        encoding="utf-8",
                    )
            except Exception:
                pass

        def _write_run_log(prefix: str, stdout_text: str, stderr_text: str) -> str:
            run_log = f"{prefix}\n{stdout_text}\n\nSTDERR:\n{stderr_text}".strip()
            if capture is not None:
                from nichebench.execution.runtime.capture.scrub import scrub_known_credentials

                run_log = scrub_known_credentials(run_log, capture.known_credentials)[0]
            (output_island_host / "run.log").write_text(run_log, encoding="utf-8")
            return run_log

        try:
            run_start = datetime.now(tz=timezone.utc)
            if not use_popen_path:
                # --- Historical subprocess.run path (watchdog off, capture off) ---
                try:
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout_seconds,
                        check=False,
                    )
                    run_end = datetime.now(tz=timezone.utc)
                    run_log = _write_run_log("STDOUT:", result.stdout, result.stderr)
                    if result.returncode != 0:
                        _salvage_session_state(datetime.now(tz=timezone.utc))
                        raise RuntimeError(
                            result.stderr.strip() or f"Container OpenCode command failed with exit {result.returncode}"
                        )

                    # Best-effort trajectory capture from cage state SQLite
                    trajectory: Optional[Dict[str, Any]] = None
                    try:
                        system_prompt = self._read_workspace_system_prompt(workspace_host_path)
                        trajectory = self._build_trajectory_from_sqlite(
                            db_path=db_path,
                            test_case_id=test_case.id,
                            model_str=self.mut_model_str,
                            start_time=run_start,
                            end_time=run_end,
                            system_prompt=system_prompt,
                        )
                    except Exception:
                        pass  # Trajectory capture is best-effort; never crash the run

                    return result.stdout.strip(), task_input, run_log, island_topology, image, trajectory
                except subprocess.TimeoutExpired as exc:
                    _force_remove_cage_container()
                    run_end = datetime.now(tz=timezone.utc)
                    raw_stdout = exc.stdout or ""
                    raw_stderr = exc.stderr or ""
                    partial_stdout = (
                        raw_stdout.decode("utf-8", errors="replace") if isinstance(raw_stdout, bytes) else raw_stdout
                    )
                    partial_stderr = (
                        raw_stderr.decode("utf-8", errors="replace") if isinstance(raw_stderr, bytes) else raw_stderr
                    )
                    run_log = _write_run_log("STDOUT (partial, timeout):", partial_stdout, partial_stderr)

                    try:
                        system_prompt = self._read_workspace_system_prompt(workspace_host_path)
                        partial_trajectory = self._build_trajectory_from_sqlite(
                            db_path=db_path,
                            test_case_id=test_case.id,
                            model_str=self.mut_model_str,
                            start_time=run_start,
                            end_time=run_end,
                            system_prompt=system_prompt,
                        )
                        if partial_trajectory:
                            (output_island_host / "opencode_partial_trajectory.json").write_text(
                                json.dumps(partial_trajectory, indent=2),
                                encoding="utf-8",
                            )
                    except Exception:
                        pass

                    try:
                        raw_dump = self._dump_opencode_session_state(db_path)
                        if raw_dump:
                            (output_island_host / "opencode_session_dump.json").write_text(
                                json.dumps(raw_dump, indent=2),
                                encoding="utf-8",
                            )
                    except Exception:
                        pass

                    _finalize_capture("timeout")
                    raise RuntimeError(f"Container OpenCode command timed out after {timeout_seconds} seconds") from exc

            # --- Popen + watchdog polling path (watchdog enabled or capture on) ---
            poll_seconds = float(runtime_config.get("runtime_watchdog_poll_seconds", 5))
            stop_idle_seconds = float(runtime_config.get("runtime_watchdog_stop_idle_seconds", 240))
            inactivity_seconds = float(runtime_config.get("runtime_watchdog_inactivity_seconds", 600))

            # With capture on, the reader threads must be able to see EOF, so the
            # child gets its own process group: signalling the group tears down
            # the whole tree (the docker CLI and anything it spawned) instead of
            # leaving a grandchild holding the pipes open.
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=capture is not None,
            )
            stdout_chunks: List[str] = []
            stderr_chunks: List[str] = []

            def _reader(stream: Any, buf: List[str], spool: Any = None) -> None:
                if stream is None:
                    return
                try:
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    while True:
                        raw = stream.read1(65536) if hasattr(stream, "read1") else stream.readline()
                        if not raw:
                            buf.append(decoder.decode(b"", final=True))
                            break
                        if spool is not None:
                            try:
                                spool.write(raw)
                            except (CaptureError, OSError) as exc:
                                # Disk full or quota reached: stop capturing,
                                # keep draining the pipe so the agent is never
                                # blocked on a full archive.
                                if capture is not None:
                                    capture.stop_capture(str(exc))
                                spool = None
                        buf.append(decoder.decode(raw))
                except Exception as exc:
                    if capture is not None:
                        capture.stop_capture(f"stream_reader_failed:{type(exc).__name__}")

            t_stdout = Thread(target=_reader, args=(proc.stdout, stdout_chunks, capture_spool_stdout))
            t_stderr = Thread(target=_reader, args=(proc.stderr, stderr_chunks, capture_spool_stderr))
            t_stdout.daemon = True
            t_stderr.daemon = True
            t_stdout.start()
            t_stderr.start()

            last_db_marker: Optional[str] = None
            last_activity_mono = time.monotonic()
            run_start_mono = time.monotonic()
            watchdog_marker: Optional[str] = None

            def _signal_process(sig: int) -> None:
                """Signal the child, or its whole group when it owns one."""
                if proc.poll() is not None:
                    return
                try:
                    if capture is not None:
                        os.killpg(os.getpgid(proc.pid), sig)
                    else:
                        proc.send_signal(sig)
                except (OSError, ProcessLookupError):
                    with contextlib.suppress(Exception):
                        proc.terminate()

            def _terminate_and_drain() -> None:
                with contextlib.suppress(Exception):
                    if proc.poll() is None:
                        _signal_process(signal.SIGTERM)
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            _signal_process(signal.SIGKILL)
                            proc.wait(timeout=5)
                t_stdout.join(timeout=STREAM_DRAIN_TIMEOUT_SECONDS)
                t_stderr.join(timeout=STREAM_DRAIN_TIMEOUT_SECONDS)
                _force_remove_cage_container()

            def _drain_for_capture() -> Tuple[str, str, bool, bool]:
                t_stdout.join(timeout=STREAM_DRAIN_TIMEOUT_SECONDS)
                t_stderr.join(timeout=STREAM_DRAIN_TIMEOUT_SECONDS)
                stdout_truncated = t_stdout.is_alive()
                stderr_truncated = t_stderr.is_alive()
                stdout_text = "".join(stdout_chunks)
                stderr_text = "".join(stderr_chunks)
                if stdout_truncated or stderr_truncated:
                    note = (
                        f"\n\nWARNING: reader thread(s) did not finish within 5s "
                        f"(stdout_truncated={stdout_truncated}, "
                        f"stderr_truncated={stderr_truncated}); log may be incomplete."
                    )
                    if stdout_truncated:
                        stdout_text = stdout_text + note
                    if stderr_truncated:
                        stderr_text = stderr_text + note
                return stdout_text, stderr_text, stdout_truncated, stderr_truncated

            def _write_partial_artifacts(run_end: datetime, stdout_text: str, stderr_text: str) -> None:
                try:
                    system_prompt = self._read_workspace_system_prompt(workspace_host_path)
                    partial_trajectory = self._build_trajectory_from_sqlite(
                        db_path=db_path,
                        test_case_id=test_case.id,
                        model_str=self.mut_model_str,
                        start_time=run_start,
                        end_time=run_end,
                        system_prompt=system_prompt,
                    )
                    if partial_trajectory:
                        if capture is not None:
                            from nichebench.execution.runtime.capture.scrub import scrub_known_credentials

                            partial_trajectory = json.loads(
                                scrub_known_credentials(json.dumps(partial_trajectory), capture.known_credentials)[0]
                            )
                        (output_island_host / "opencode_partial_trajectory.json").write_text(
                            json.dumps(partial_trajectory, indent=2),
                            encoding="utf-8",
                        )
                except Exception:
                    pass
                try:
                    raw_dump = self._dump_opencode_session_state(db_path)
                    if raw_dump:
                        if capture is not None:
                            from nichebench.execution.runtime.capture.scrub import scrub_known_credentials

                            raw_dump = json.loads(
                                scrub_known_credentials(json.dumps(raw_dump), capture.known_credentials)[0]
                            )
                        (output_island_host / "opencode_session_dump.json").write_text(
                            json.dumps(raw_dump, indent=2),
                            encoding="utf-8",
                        )
                except Exception:
                    pass

            try:
                while proc.poll() is None:
                    time.sleep(min(poll_seconds, 0.25) if capture is not None else poll_seconds)
                    if capture is not None and capture.capture_stopped:
                        termination_reason = "capture_storage_failed"
                        raise RuntimeError("Runtime capture storage failed; agent execution stopped")

                    # Hard timeout guard — replicates subprocess.TimeoutExpired behaviour
                    if time.monotonic() - run_start_mono >= timeout_seconds:
                        termination_reason = "timeout"
                        _terminate_and_drain()
                        run_end = datetime.now(tz=timezone.utc)
                        partial_stdout, partial_stderr, _, _ = _drain_for_capture()
                        _write_run_log("STDOUT (partial, timeout):", partial_stdout, partial_stderr)
                        _write_partial_artifacts(run_end, partial_stdout, partial_stderr)
                        _finalize_capture("timeout")
                        raise RuntimeError(f"Container OpenCode command timed out after {timeout_seconds} seconds")

                    # Watchdog DB polling
                    if db_path.exists():
                        if capture is not None and capture_attempt is not None:
                            # Keep a WAL-consistent snapshot current so a host
                            # kill mid-run still leaves session state on disk.
                            capture.checkpoint_session(capture_attempt)
                        if not watchdog_enable:
                            continue
                        db_marker, has_stop = self._poll_opencode_db(db_path)
                        if db_marker is not None and db_marker != last_db_marker:
                            last_db_marker = db_marker
                            last_activity_mono = time.monotonic()
                        idle_secs = time.monotonic() - last_activity_mono
                        watchdog_marker = self._resolve_watchdog_marker(
                            has_stop, idle_secs, stop_idle_seconds, inactivity_seconds
                        )
                        if watchdog_marker:
                            termination_reason = watchdog_marker
                            _terminate_and_drain()
                            break
            except BaseException as exc:
                # Any exception (including KeyboardInterrupt) escaping the polling
                # loop must terminate the child process, force-remove the Docker
                # container, and finalise the capture before state is deleted.
                with contextlib.suppress(Exception):
                    _terminate_and_drain()
                stdout_text, stderr_text, _, _ = _drain_for_capture()
                run_end = datetime.now(tz=timezone.utc)
                _write_run_log("STDOUT (partial, interrupted):", stdout_text, stderr_text)
                _write_partial_artifacts(run_end, stdout_text, stderr_text)
                _finalize_capture(f"interrupted:{type(exc).__name__}")
                raise

            # Join reader threads after exit (normal or watchdog break).
            stdout_text, stderr_text, stdout_truncated, stderr_truncated = _drain_for_capture()
            if capture is not None:
                if stdout_truncated or stderr_truncated:
                    capture.mark_incomplete("process_stream_drain_incomplete")
                if capture.capture_stopped:
                    raise RuntimeError("Runtime capture storage failed; agent execution stopped")

            if watchdog_marker:
                run_end = datetime.now(tz=timezone.utc)
                _write_run_log("STDOUT (partial, watchdog):", stdout_text, stderr_text)
                _write_partial_artifacts(run_end, stdout_text, stderr_text)
                _finalize_capture(watchdog_marker)
                raise RuntimeError(
                    f"{watchdog_marker} Agent execution terminated by watchdog "
                    f"after {time.monotonic() - run_start_mono:.0f}s"
                )

            # Normal process exit
            run_end = datetime.now(tz=timezone.utc)
            run_log = _write_run_log("STDOUT:", stdout_text, stderr_text)
            if proc.returncode != 0:
                # Salvage the session store before raising: a non-zero exit is one
                # of the most useful traces to have, and the state directory is
                # about to be removed.
                _write_partial_artifacts(run_end, stdout_text, stderr_text)
                _finalize_capture(f"nonzero_exit:{proc.returncode}")
                raise RuntimeError(
                    stderr_text.strip() or f"Container OpenCode command failed with exit {proc.returncode}"
                )

            # Best-effort trajectory capture from cage state SQLite
            trajectory = None
            try:
                system_prompt = self._read_workspace_system_prompt(workspace_host_path)
                trajectory = self._build_trajectory_from_sqlite(
                    db_path=db_path,
                    test_case_id=test_case.id,
                    model_str=self.mut_model_str,
                    start_time=run_start,
                    end_time=run_end,
                    system_prompt=system_prompt,
                )
                if capture is not None and trajectory is not None:
                    from nichebench.execution.runtime.capture.scrub import scrub_known_credentials

                    trajectory = json.loads(
                        scrub_known_credentials(json.dumps(trajectory), capture.known_credentials)[0]
                    )
            except Exception:
                pass  # Trajectory capture is best-effort; never crash the run

            # Parse the agent's JSON event stream and derive the assistant's
            # final text. With --format json the raw stdout is a JSONL event log,
            # not prose, so returning it verbatim would put the whole event log
            # into the judge prompt and the report. The raw bytes stay in the
            # stdout spool; only the derived text becomes mut_output.
            mut_output = stdout_text.strip()
            if capture is not None and capture_journal is not None:
                from nichebench.execution.runtime.capture.agent_stream import parse_opencode_json_stream

                summary = parse_opencode_json_stream(stdout_text.splitlines(), lambda event: None)
                mut_output = str(summary["assistant_text"]).strip()
                from nichebench.execution.runtime.capture.scrub import scrub_known_credentials

                mut_output = scrub_known_credentials(mut_output, capture.known_credentials)[0]
                # No raw-JSON fallback, even when the agent emitted only tools,
                # reasoning, or malformed output. Scoring sees assistant text.
                capture.record_agent_stream(capture_journal, stdout_text, mut_output)

            termination_reason = "normal_exit"
            _finalize_capture(termination_reason)
            if capture is not None and capture.capture_stopped:
                raise RuntimeError("Runtime capture storage failed during finalisation")
            return mut_output, task_input, run_log, island_topology, image, trajectory
        finally:
            if capture is not None:
                # Idempotent: finalise() is a no-op once the descriptor is written,
                # so the normal path above already closed journals and stopped the
                # proxy; this guarantees the failure path never skips finalisation.
                _finalize_capture(termination_reason)
            shutil.rmtree(state_root_tmp, ignore_errors=True)
