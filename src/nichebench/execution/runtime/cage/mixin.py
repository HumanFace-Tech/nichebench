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

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.runtime.cage import retry as cage_retry


def _executor_globals() -> Any:
    """Return the orchestrator module for backward-compatibility patch points.

    Allows runtime submodules to access re-exported symbols (``subprocess``,
    ``ValidationError``, ``RuntimeScorer``, etc.) that the test-harness
    monkey-patches at load time without touching the actual runtime code.
    """
    from nichebench.execution import orchestrator

    return orchestrator


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
        profile,
        timeout_seconds: int,
        task_input_override: Optional[str] = None,
    ) -> Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]]]:
        """Run OpenCode inside a dedicated container with Docker socket access.

        Spawns a cage container that:

        * Mounts the workspace at its real host path (required for DDEV bind mounts)
        * Exposes input, output/trace, and optional ops islands for I/O isolation
        * Writes ``opencode.json`` for the MUT's model binding
        * Installs a cage-local ``git`` wrapper that blocks unsafe operations
        * Polls the OpenCode SQLite DB for watchdog (idle/inactivity) termination
        * Captures best-effort trajectory from the session SQLite on exit

        When ``runtime_watchdog_enable`` is true (default) a ``Popen``+polling loop
        handles idle timeouts; otherwise a simple ``subprocess.run`` call is used.

        Args:
            test_case: Test case specification.
            workspace: ``Workspace`` instance (runtime task branch checkout).
            agent_manifest: Agent configuration from the task manifest.
            runtime_config: Full runtime configuration dict.
            profile: Resolved profile object.
            timeout_seconds: Hard timeout for the container command.
            task_input_override: If set, overrides task input (used for the
                second pass in the two-pass review nudge flow).

        Returns:
            Tuple of (mut_output, user_input, run_log, island_topology,
            effective_image, trajectory).  ``trajectory`` is ``None`` if capture
            fails (best-effort); ``run_log`` always contains stdout/stderr.
        """
        # Import helpers locally to avoid circular imports at module level

        if isinstance(workspace, _executor_globals().Workspace):
            workspace_host_path = Path(workspace.path).resolve()
        else:
            workspace_host_path = Path(workspace.path).resolve()

        # Use override if provided (e.g., review nudge for second pass), otherwise read from TASK.md
        if task_input_override is not None:
            task_input = task_input_override
        else:
            prompt = getattr(test_case, "prompt", "") or test_case.raw.get("prompt", "") or ""
            context = getattr(test_case, "context", "") or test_case.raw.get("context", "") or ""
            task_input = prompt if not context else f"{prompt}\n\nContext:\n{context}"
            task_markdown_path = workspace_host_path / "TASK.md"
            try:
                task_markdown = task_markdown_path.read_text(encoding="utf-8").strip()
                if task_markdown:
                    task_input = task_markdown
            except OSError:
                pass

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

        self._write_cage_opencode_json(
            workspace_host_path=workspace_host_path,
            opencode_provider=opencode_provider,
            opencode_model_id=opencode_model_id,
            api_base=cage_api_base,
            runtime_config=runtime_config,
        )

        # Run-scoped OpenCode state roots prevent any inheritance from host user
        # state and stay outside of task workspace to avoid diff pollution.
        state_root_tmp = tempfile.mkdtemp(prefix="nichebench-cage-state-")
        state_root = Path(state_root_tmp)
        home_host = state_root / "home"
        xdg_config_host = state_root / "xdg-config"
        xdg_data_host = state_root / "xdg-data"
        xdg_state_host = state_root / "xdg-state"
        xdg_cache_host = state_root / "xdg-cache"
        bin_host = state_root / "bin"
        for path in (home_host, xdg_config_host, xdg_data_host, xdg_state_host, xdg_cache_host, bin_host):
            path.mkdir(parents=True, exist_ok=True)
        self._write_cage_git_wrapper(bin_host)

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
            env["OPENAI_BASE_URL"] = cage_api_base
            env["OPENAI_API_KEY"] = "dummy"

        # Resolve effective cage image (handles DDEV capability checks and auto-build)
        image = self._resolve_effective_cage_image(runtime_config)
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
        command.extend(
            [
                "--entrypoint",
                "opencode",
                image,
                "run",
                "--pure",
                "--dangerously-skip-permissions",
                "--model",
                opencode_model_binding,
                task_input,
            ]
        )

        db_path = xdg_data_host / "opencode" / "opencode.db"
        watchdog_enable = bool(runtime_config.get("runtime_watchdog_enable", True))

        def _force_remove_cage_container() -> None:
            subprocess.run(["docker", "rm", "-f", container_name], check=False, capture_output=True, text=True)

        try:
            run_start = datetime.now(tz=timezone.utc)
            if not watchdog_enable:
                # --- Original subprocess.run path (watchdog disabled) ---
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
                    run_log = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}".strip()
                    (output_island_host / "run.log").write_text(run_log, encoding="utf-8")
                    if result.returncode != 0:
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
                    run_log = (
                        f"STDOUT (partial, timeout):\n{partial_stdout}\n\n"
                        f"STDERR (partial, timeout):\n{partial_stderr}"
                    ).strip()
                    (output_island_host / "run.log").write_text(run_log, encoding="utf-8")

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

                    raise RuntimeError(f"Container OpenCode command timed out after {timeout_seconds} seconds") from exc
            else:
                # --- Popen + watchdog polling path (watchdog enabled) ---
                poll_seconds = float(runtime_config.get("runtime_watchdog_poll_seconds", 5))
                stop_idle_seconds = float(runtime_config.get("runtime_watchdog_stop_idle_seconds", 240))
                inactivity_seconds = float(runtime_config.get("runtime_watchdog_inactivity_seconds", 600))

                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                stdout_chunks: List[str] = []
                stderr_chunks: List[str] = []

                def _reader(stream: Any, buf: List[str]) -> None:
                    if stream is None:
                        return
                    try:
                        for line in stream:
                            buf.append(line)
                    except Exception:
                        pass

                t_stdout = Thread(target=_reader, args=(proc.stdout, stdout_chunks), daemon=True)
                t_stderr = Thread(target=_reader, args=(proc.stderr, stderr_chunks), daemon=True)
                t_stdout.start()
                t_stderr.start()

                last_db_marker: Optional[str] = None
                last_activity_mono = time.monotonic()
                run_start_mono = time.monotonic()
                watchdog_marker: Optional[str] = None

                # Guard: if any exception escapes the polling loop (SQLite error,
                # artifact write error, etc.), ensure the Docker container and child
                # process are terminated before the error propagates.  Without this
                # guard, a transient polling bug can leak the container and process
                # to the host, contaminating later benchmark runs.
                try:
                    while proc.poll() is None:
                        time.sleep(poll_seconds)

                        # Hard timeout guard — replicates subprocess.TimeoutExpired behaviour
                        if time.monotonic() - run_start_mono >= timeout_seconds:
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait()
                            t_stdout.join(timeout=5)
                            t_stderr.join(timeout=5)
                            _force_remove_cage_container()
                            run_end = datetime.now(tz=timezone.utc)
                            partial_stdout = "".join(stdout_chunks)
                            partial_stderr = "".join(stderr_chunks)
                            run_log = (
                                f"STDOUT (partial, timeout):\n{partial_stdout}\n\n"
                                f"STDERR (partial, timeout):\n{partial_stderr}"
                            ).strip()
                            (output_island_host / "run.log").write_text(run_log, encoding="utf-8")
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
                            raise RuntimeError(f"Container OpenCode command timed out after {timeout_seconds} seconds")

                        # Watchdog DB polling
                        if db_path.exists():
                            db_marker, has_stop = self._poll_opencode_db(db_path)
                            if db_marker is not None and db_marker != last_db_marker:
                                last_db_marker = db_marker
                                last_activity_mono = time.monotonic()
                            idle_secs = time.monotonic() - last_activity_mono
                            watchdog_marker = self._resolve_watchdog_marker(
                                has_stop, idle_secs, stop_idle_seconds, inactivity_seconds
                            )
                            if watchdog_marker:
                                proc.terminate()
                                try:
                                    proc.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    proc.kill()
                                    proc.wait()
                                _force_remove_cage_container()
                                break
                except BaseException:
                    # Any exception (including KeyboardInterrupt) escaping the polling
                    # loop must terminate the child process and force-remove the
                    # Docker container.  Without this, transient polling/SQLite errors
                    # can leak the container to the host between benchmark runs.
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                with contextlib.suppress(Exception):
                                    proc.kill()
                                    proc.wait()
                    except Exception:
                        pass
                    try:
                        t_stdout.join(timeout=5)
                        t_stderr.join(timeout=5)
                    except Exception:
                        pass
                    with contextlib.suppress(Exception):
                        _force_remove_cage_container()
                    raise

                # Join reader threads after exit (normal or watchdog break).
                # A 5s timeout is the historical default; if readers are still alive
                # we proceed anyway but record a truncation marker so diagnostics
                # know the captured log may be incomplete.
                t_stdout.join(timeout=5)
                t_stderr.join(timeout=5)
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

                if watchdog_marker:
                    run_end = datetime.now(tz=timezone.utc)
                    run_log = (
                        f"STDOUT (partial, watchdog):\n{stdout_text}\n\n" f"STDERR (partial, watchdog):\n{stderr_text}"
                    ).strip()
                    (output_island_host / "run.log").write_text(run_log, encoding="utf-8")
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
                    raise RuntimeError(
                        f"{watchdog_marker} Agent execution terminated by watchdog "
                        f"after {time.monotonic() - run_start_mono:.0f}s"
                    )

                # Normal process exit
                run_end = datetime.now(tz=timezone.utc)
                run_log = f"STDOUT:\n{stdout_text}\n\nSTDERR:\n{stderr_text}".strip()
                (output_island_host / "run.log").write_text(run_log, encoding="utf-8")
                if proc.returncode != 0:
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
                except Exception:
                    pass  # Trajectory capture is best-effort; never crash the run

                return stdout_text.strip(), task_input, run_log, island_topology, image, trajectory
        finally:
            shutil.rmtree(state_root_tmp, ignore_errors=True)
