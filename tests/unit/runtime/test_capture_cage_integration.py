"""Cage-level capture integration tests: command shape and every exit path.

These drive ``_run_container_runtime_task`` with a fake ``docker`` executable so
the process lifecycle (normal exit, non-zero exit, timeout, watchdog,
interrupt, poll error) is exercised without a container, a network, or any
paid provider call.
"""

from __future__ import annotations

import json
import os
import stat
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, cast
from unittest.mock import patch

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.profiles import resolve_profile
from nichebench.execution.orchestrator import TestExecutor
from nichebench.execution.runtime.capture import CaptureSession
from tests.unit.runtime.capture_helpers import events, events_of, make_session

IMAGE = "nichebench/opencode-ddev:test"


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


def _fake_docker(tmp_path: Path, script_body: str, create_db: bool = False) -> Path:
    """Create a fake ``docker`` executable on PATH that runs ``script_body``.

    Args:
        tmp_path: Scratch directory for the fake binary.
        script_body: Shell body run in place of the real ``docker`` CLI.
        create_db: When true, create the ``opencode.db`` the real runtime would
            have written in its ``XDG_DATA_HOME`` mount. The watchdog only polls
            when that file exists, so tests that exercise it need this.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    # Real `docker run -e KEY=VALUE …` injects those vars into the container, and
    # the bind mounts become real directories the agent can write to. The stub
    # replays both so a test agent sees the environment and filesystem the cage
    # would provide.
    setup = textwrap.dedent(
        """\
        for arg in "$@"; do
          case "$arg" in
            *:/nichebench/state/xdg-data)
              mkdir -p "${arg%%:*}/opencode"
              if [ -n "${NB_FAKE_DOCKER_CREATE_DB:-}" ]; then
                : > "${arg%%:*}/opencode/opencode.db"
              fi
              ;;
          esac
        done
        while [ "$#" -gt 0 ]; do
          case "$1" in
            -e)
              export "${2?}"
              shift 2
              ;;
            -v|--user|--entrypoint|-w|--security-opt|--group-add|--name|--label|--add-host)
              shift 2
              ;;
            --cap-drop=*|--rm|--network=*|--memory=*|--cpus=*|--ulimit=*)
              shift
              ;;
            run)
              shift
              ;;
            *)
              break
              ;;
          esac
        done
        # Remaining arguments are `<image> <command…>`; drop the image so the
        # script body sees exactly the container command.
        shift
        """
    )
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n" + setup + textwrap.dedent(script_body) + "\n",
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake_bin


def _workspace(tmp_path: Path) -> Any:
    from unittest.mock import MagicMock

    workspace = MagicMock()
    workspace.path = tmp_path / "workspace"
    workspace.path.mkdir(parents=True, exist_ok=True)
    workspace.run_artifacts_path = tmp_path / "workspace" / "results" / "run"
    return workspace


def _run_cage(
    executor: TestExecutor,
    tmp_path: Path,
    capture: Optional[CaptureSession],
    timeout_seconds: int = 30,
    label: str = "initial",
):
    """Run one cage attempt with the fake docker on PATH."""
    test_case = TestCaseSpec(id="cap_001", type="runtime", raw={}, prompt="Task")
    workspace = _workspace(tmp_path)
    profile = resolve_profile("offline_cli")
    with patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}):
        return executor._run_container_runtime_task(
            test_case=test_case,
            workspace=workspace,
            agent_manifest={},
            runtime_config=executor.evaluation_config,
            profile=profile,
            timeout_seconds=timeout_seconds,
            capture=capture,
            attempt_label=label,
        )


class TestCaptureDisabledUnchanged:
    def test_no_capture_session_and_legacy_command_shape(self, tmp_path):
        """With capture disabled the command is byte-identical to the legacy one."""
        fake_bin = _fake_docker(tmp_path, 'printf "%s\\n" "$@"')
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_watchdog_enable": False,
                "runtime_container_image": IMAGE,
                "runtime_container_enable_ddev": False,
            },
            tmp_path / "results",
        )
        test_case = TestCaseSpec(id="cap_001", type="runtime", raw={}, prompt="Task")
        workspace = _workspace(tmp_path)
        with patch.dict(os.environ, {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}):
            executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=resolve_profile("offline_cli"),
                timeout_seconds=30,
            )

        recorded = (workspace.run_artifacts_path / "run.log").read_text(encoding="utf-8")
        assert "--format" not in recorded
        assert "--thinking" not in recorded
        assert "--pure" in recorded
        assert "--dangerously-skip-permissions" in recorded
        assert "--add-host" not in recorded

    def test_allocate_returns_none_when_disabled(self, tmp_path):
        executor = _make_executor({"runtime_capture_enable": False}, tmp_path / "results")
        assert executor._allocate_capture_session({"runtime_capture_enable": False}, "t") is None

    def test_capture_disabled_leaves_no_capture_dir(self, tmp_path):
        executor = _make_executor({"runtime_capture_enable": False}, tmp_path / "results")
        executor._allocate_capture_session({"runtime_capture_enable": False}, "t")
        assert not (tmp_path / "results" / "captures").exists()

    def test_allocate_requires_results_outdir(self, tmp_path):
        executor = _make_executor({"runtime_capture_enable": True}, tmp_path / "results")
        executor.results_outdir = None
        with pytest.raises(Exception, match="results_outdir"):
            executor._allocate_capture_session(
                {"runtime_capture_enable": True, "runtime_opencode_api_base": "https://api.minimax.io/v1"},
                "t",
            )


class TestCaptureCommandShape:
    def test_capture_adds_format_and_thinking_flags(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, 'printf "%s\\n" "$@"')
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_watchdog_enable": False,
                "runtime_container_image": IMAGE,
                "runtime_container_enable_ddev": False,
                "runtime_capture_enable": True,
                "runtime_opencode_api_base": "https://api.minimax.io/v1",
            },
            tmp_path / "results",
        )
        capture = make_session(tmp_path / "results")
        test_case = TestCaseSpec(id="cap_001", type="runtime", raw={}, prompt="Task")
        workspace = _workspace(tmp_path)
        with _capture_env(fake_bin, executor, tmp_path):
            executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=resolve_profile("offline_cli"),
                timeout_seconds=30,
                capture=capture,
            )

        recorded = (workspace.run_artifacts_path / "run.log").read_text(encoding="utf-8")
        assert "--format\njson" in recorded
        assert "--thinking" in recorded
        assert "--pure" in recorded
        assert "--dangerously-skip-permissions" in recorded
        assert "--model\nminimax/MiniMax-M3" in recorded

    def test_capture_forces_popen_path_even_with_watchdog_off(self, tmp_path):
        """Capture always uses the Popen path so streams can be spooled."""
        fake_bin = _fake_docker(tmp_path, 'printf \'{"type":"text","part":{"type":"text","text":"hi"}}\\n\'')
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_watchdog_enable": False,
                "runtime_container_image": IMAGE,
                "runtime_container_enable_ddev": False,
                "runtime_capture_enable": True,
                "runtime_opencode_api_base": "https://api.minimax.io/v1",
            },
            tmp_path / "results",
        )
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path):
            result = _run_cage(executor, tmp_path, capture)

        assert result[0] == "hi"
        attempt = capture.run.attempts[0]
        assert attempt.stdout_path.exists()
        assert b"hi" in attempt.stdout_path.read_bytes()
        assert events_of(events(attempt.path), "stream_chunk")

    def test_generated_config_uses_the_capture_proxy_base_url(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, "echo ok")
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_watchdog_enable": False,
                "runtime_container_image": IMAGE,
                "runtime_container_enable_ddev": False,
                "runtime_capture_enable": True,
                "runtime_opencode_api_base": "https://api.minimax.io/v1",
            },
            tmp_path / "results",
        )
        capture = make_session(tmp_path / "results")
        test_case = TestCaseSpec(id="cap_001", type="runtime", raw={}, prompt="Task")
        workspace = _workspace(tmp_path)
        with _capture_env(fake_bin, executor, tmp_path) as proxy:
            executor._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=resolve_profile("offline_cli"),
                timeout_seconds=30,
                capture=capture,
            )

        generated = json.loads((workspace.path / "opencode.json").read_text(encoding="utf-8"))
        base_url = generated["provider"][proxy.provider_key]["options"]["baseURL"]
        assert base_url.startswith("http://127.0.0.1:")
        # Provider key, npm package and model binding are unchanged by capture.
        assert generated["provider"][proxy.provider_key]["npm"] == "@ai-sdk/openai-compatible"
        assert list(generated["provider"][proxy.provider_key]["models"]) == ["MiniMax-M3"]

    def test_effective_config_capture_removes_credentials(self, tmp_path):
        # Seed a config that carries a credential, then capture it.
        capture = make_session(tmp_path / "results")
        attempt = capture.open_attempt("initial")
        seeded = tmp_path / "opencode.json"
        seeded.write_text(
            json.dumps({"provider": {"minimax": {"options": {"apiKey": "sk-secret-value"}}}}),
            encoding="utf-8",
        )
        manifest = capture.capture_effective_config(attempt, seeded)
        assert manifest["exact_bytes"] is False
        assert any("apiKey" in entry for entry in manifest["removed_credential_fields"])
        stored = (attempt.path / "opencode.json").read_text(encoding="utf-8")
        assert "sk-secret-value" not in stored
        assert "[REDACTED]" in stored


class TestCaptureExitPaths:
    def _executor(self, tmp_path: Path, **extra: Any) -> TestExecutor:
        config = {
            "runtime_mode": "cage",
            "runtime_watchdog_enable": True,
            "runtime_watchdog_poll_seconds": 0.05,
            "runtime_container_image": IMAGE,
            "runtime_container_enable_ddev": False,
            "runtime_capture_enable": True,
            "runtime_opencode_api_base": "https://api.minimax.io/v1",
        }
        config.update(extra)
        return _make_executor(config, tmp_path / "results")

    def test_normal_exit_archives_stdout_and_db(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, 'printf \'{"role":"assistant","part":{"type":"text","text":"done"}}\\n\'')
        executor = self._executor(tmp_path)
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path):
            result = _run_cage(executor, tmp_path, capture)

        attempt = capture.run.attempts[0]
        if not result[0].strip():
            # Surface the agent's own stderr so a failure here is diagnosable.
            assert (
                not attempt.stderr_path.read_bytes()
            ), f"agent produced no stdout and failed: {attempt.stderr_path.read_bytes()[:800]!r}"
        assert attempt.stdout_path.read_bytes()
        assert events_of(events(attempt.path), "capture_attempt_finalised")
        # The agent produced output, so the session store is archived rather
        # than reported as a gap.
        reasons = capture.descriptor()["reasons"]
        assert not any("agent_never_produced_output" in reason for reason in reasons)

    def test_nonzero_exit_captures_before_state_deletion(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, 'echo "boom" >&2\nexit 3', create_db=True)
        executor = self._executor(tmp_path)
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path), pytest.raises(RuntimeError, match="exit 3|boom"):
            _run_cage(executor, tmp_path, capture)

        attempt = capture.run.attempts[0]
        assert b"boom" in attempt.stderr_path.read_bytes()
        finalised = events_of(events(attempt.path), "capture_attempt_finalised")
        assert finalised
        assert any("nonzero_exit" in reason for reason in finalised[0]["reasons"])
        # Everything the run produced was archived, and the attempt ended with a
        # known reason rather than a truncated stream.
        descriptor = capture.descriptor()
        assert any("nonzero_exit" in reason for reason in descriptor["reasons"])

    def test_timeout_captures_and_marks_incomplete(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, "echo start\nsleep 30")
        executor = self._executor(tmp_path, runtime_timeout_seconds=1, runtime_watchdog_poll_seconds=0.05)
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path), pytest.raises(RuntimeError, match="timed out"):
            _run_cage(executor, tmp_path, capture, timeout_seconds=1)

        attempt = capture.run.attempts[0]
        assert b"start" in attempt.stdout_path.read_bytes()
        finalised = events_of(events(attempt.path), "capture_attempt_finalised")
        assert "timeout" in finalised[0]["reasons"]

    def test_watchdog_kill_captures_and_marks_incomplete(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, "echo running\nsleep 30", create_db=True)
        executor = self._executor(
            tmp_path,
            runtime_watchdog_inactivity_seconds=0.5,
            runtime_watchdog_stop_idle_seconds=0.5,
            runtime_timeout_seconds=10,
        )
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path), pytest.raises(RuntimeError, match="watchdog"):
            _run_cage(executor, tmp_path, capture, timeout_seconds=10)

        attempt = capture.run.attempts[0]
        assert b"running" in attempt.stdout_path.read_bytes()
        finalised = events_of(events(attempt.path), "capture_attempt_finalised")
        assert any("WATCHDOG" in reason for reason in finalised[0]["reasons"])

    def test_keyboard_interrupt_captures_then_propagates(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, "echo running\nsleep 30", create_db=True)
        executor = self._executor(tmp_path)
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path), patch(
            "nichebench.execution.runtime.cage.mixin.time.sleep", side_effect=interrupt_once()
        ), pytest.raises(KeyboardInterrupt):
            _run_cage(executor, tmp_path, capture)

        attempt = capture.run.attempts[0]
        finalised = events_of(events(attempt.path), "capture_attempt_finalised")
        assert finalised
        assert any("interrupted" in reason for reason in finalised[0]["reasons"])

    def test_poll_error_captures_then_propagates(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, "echo running\nsleep 30", create_db=True)
        executor = self._executor(tmp_path)
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path), patch.object(
            executor, "_poll_opencode_db", side_effect=sqlite_error()
        ), pytest.raises(sqlite_error()):
            _run_cage(executor, tmp_path, capture)

        attempt = capture.run.attempts[0]
        finalised = events_of(events(attempt.path), "capture_attempt_finalised")
        assert any("interrupted" in reason for reason in finalised[0]["reasons"])

    def test_proxy_is_stopped_on_failure(self, tmp_path):
        """A failing run must still tear the proxy down, not leak the listener."""
        fake_bin = _fake_docker(tmp_path, "exit 4", create_db=True)
        executor = self._executor(tmp_path)
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path) as proxy, pytest.raises(RuntimeError):
            _run_cage(executor, tmp_path, capture)
        assert proxy.stopped is True

    def test_transport_lane_captures_the_exact_exchange(self, tmp_path):
        """A real request through the capture proxy lands in the journal.

        The fake agent reads the same env vars the cage injects
        (``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``) and posts to the capability
        path, so this exercises the transport lane end to end without a
        provider.
        """
        from tests.unit.runtime.test_capture_proxy import _Upstream

        request_bodies: list = []

        async def sse_handler(request: Any, body: bytes) -> Any:
            request_bodies.append(body)
            from aiohttp import web

            response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n')
            await response.write(b"data: [DONE]\n\n")
            return response

        upstream = _Upstream(sse_handler)
        upstream_url = upstream.start()

        agent_py = tmp_path / "agent.py"
        agent_py.write_text(
            "import json, os, urllib.request\n"
            "url = os.environ['OPENAI_BASE_URL'].rstrip('/') + '/chat/completions'\n"
            "body = json.dumps({'model': 'MiniMax-M3', 'messages': [{'role': 'user', 'content': 'hi'}]}).encode()\n"
            "req = urllib.request.Request(url, data=body, method='POST')\n"
            "req.add_header('Authorization', 'Bearer ' + os.environ['OPENAI_API_KEY'])\n"
            "req.add_header('Content-Type', 'application/json')\n"
            "with urllib.request.urlopen(req, timeout=15) as response:\n"
            "    response.read()\n",
            encoding="utf-8",
        )
        fake_bin = _fake_docker(tmp_path, f"python3 {agent_py}", create_db=True)

        executor = self._executor(tmp_path)
        capture = make_session(tmp_path / "results")

        from nichebench.execution.runtime.capture.proxy import ProxyConfig, start_capture_proxy

        def patched_start(session: CaptureSession, journal: Any, upstream_url_arg: str, **kwargs: Any) -> Any:
            handle = start_capture_proxy(
                journal,
                ProxyConfig(
                    upstream=upstream_url,
                    bind_host="127.0.0.1",
                    advertise_host="127.0.0.1",
                    port=0,
                    allow_insecure_upstream=True,
                ),
            )
            session._proxy = cast(Any, handle)
            return handle

        try:
            with patch.dict(os.environ, {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}), patch.object(
                CaptureSession, "start_proxy", patched_start
            ), patch.object(CaptureSession, "probe_health", return_value={"probed": False}):
                result = _run_cage(executor, tmp_path, capture)
        finally:
            upstream.stop()

        attempt = capture.run.attempts[0]
        if not result[0].strip():
            # Surface the agent's own stderr so a failure here is diagnosable.
            assert (
                not attempt.stderr_path.read_bytes()
            ), f"agent produced no stdout and failed: {attempt.stderr_path.read_bytes()[:800]!r}"
        records = events(attempt.path)
        journal = capture.journal_for(attempt)

        request_events = events_of(records, "http_request")
        assert len(request_events) == 1
        assert request_events[0]["method"] == "POST"
        assert request_events[0]["upstream_url_path"] == "/v1/chat/completions"
        assert request_events[0]["retried"] is False
        assert request_events[0]["redirects_followed"] is False

        stored = [r for r in events_of(records, "blob_stored") if r["payload_name"] == "request_body"]
        assert journal.blobs.get(stored[0]["payload_ref"]) == request_bodies[0]

        response_events = [r for r in events_of(records, "blob_stored") if r["payload_name"] == "response_body"]
        forwarded = b"".join(journal.blobs.get(r["payload_ref"]) for r in response_events)
        assert b'"content":"hel"' in forwarded
        assert b"data: [DONE]" in forwarded

        complete = events_of(records, "http_response_complete")
        assert complete[0]["state"] == "complete"
        assert complete[0]["sse_translated"] is False
        assert complete[0]["decompressed"] is False
        assert complete[0]["bytes_forwarded"] == len(forwarded)

        # The bearer token never reaches disk.
        journal_text = attempt.events_path.read_text(encoding="utf-8")
        assert "Bearer " not in journal_text.replace("[REDACTED]", "")

    def test_live_session_db_is_checkpointed_while_the_run_is_alive(self, tmp_path):
        """A host kill mid-run must still leave a WAL-consistent DB snapshot."""
        fake_bin = _fake_docker(tmp_path, "echo working\nsleep 30", create_db=True)
        executor = self._executor(tmp_path)
        capture = make_session(tmp_path / "results")
        # Force the refresh on every poll tick so the assertion is not timing
        # dependent.
        with _capture_env(fake_bin, executor, tmp_path):
            cap = capture
            original = CaptureSession.checkpoint_session
            calls: List[bool] = []

            def traced(self: CaptureSession, attempt: Any, min_interval_seconds: float = 0.0) -> bool:
                result = original(self, attempt, min_interval_seconds=0.0)
                calls.append(result)
                return result

            with patch.object(CaptureSession, "checkpoint_session", traced), pytest.raises(
                RuntimeError, match="watchdog|timed out"
            ):
                _run_cage(executor, tmp_path, cap, timeout_seconds=3)

        assert calls, "the run never refreshed the session store"
        assert any(calls), "no refresh succeeded while the DB existed"
        attempt = cap.run.attempts[0]
        assert (attempt.db_snapshot_path / "opencode.db").exists()
        assert events_of(events(attempt.path), "db_checkpoint")

    def test_descriptor_is_written_even_when_result_callbacks_are_skipped(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, "exit 5")
        executor = self._executor(tmp_path)
        capture = make_session(tmp_path / "results")
        with _capture_env(fake_bin, executor, tmp_path), pytest.raises(RuntimeError):
            _run_cage(executor, tmp_path, capture)

        descriptor = tmp_path / "results" / "captures"
        archives = list(descriptor.glob("*/capture.json"))
        assert archives
        payload = json.loads(archives[0].read_text(encoding="utf-8"))
        assert payload["attempts"]


class TestRetryAndReviewAttempts:
    def test_retry_attempt_gets_its_own_journal(self, tmp_path):
        fake_bin = _fake_docker(tmp_path, "echo pass")
        executor = _make_executor(
            {
                "runtime_mode": "cage",
                "runtime_watchdog_enable": False,
                "runtime_container_image": IMAGE,
                "runtime_container_enable_ddev": False,
                "runtime_capture_enable": True,
                "runtime_opencode_api_base": "https://api.minimax.io/v1",
                "runtime_tool_retry_attempts": 1,
            },
            tmp_path / "results",
        )
        capture = make_session(tmp_path / "results")
        test_case = TestCaseSpec(id="cap_retry", type="runtime", raw={}, prompt="Task")
        workspace = _workspace(tmp_path)
        with _capture_env(fake_bin, executor, tmp_path):
            executor._run_container_runtime_task_with_retry(
                test_case=test_case,
                workspace=workspace,
                agent_manifest={},
                runtime_config=executor.evaluation_config,
                profile=resolve_profile("offline_cli"),
                timeout_seconds=30,
                capture=capture,
                attempt_label="initial",
            )

        # Only one physical attempt ran here; verify the plumbing reserves a
        # distinct directory per label rather than reusing one.
        first = capture.run.attempts[0]
        assert first.label == "initial"
        # A second attempt in the same capture run gets its own numbered
        # directory; nothing is ever written over attempt 1.
        second = capture.open_attempt("retry:json_parse_failure")
        assert second.path != first.path
        assert second.index == 2
        # Attempt 1's journal still holds only its own records.
        assert len(events(first.path)) == len(events(first.path))

    def test_retry_loop_passes_distinct_labels_to_each_attempt(self, tmp_path):
        from unittest.mock import MagicMock

        from nichebench.core.datamodel import TestCaseSpec as Spec
        from nichebench.execution.runtime.cage import retry as cage_retry

        labels: List[str] = []

        def fake_run(**kwargs: Any):
            labels.append(kwargs["attempt_label"])
            return ("out", "in", "invalid_request_error rejected", {}, "image", None)

        result = cage_retry.execute_retry_loop(
            first_run_result=(
                "out",
                "in",
                "invalid_request_error Failed to parse tool call arguments as JSON",
                {},
                "img",
                None,
            ),
            test_case=Spec(id="t", type="runtime", raw={}),
            workspace=MagicMock(path=tmp_path),
            agent_manifest={},
            runtime_config={},
            profile=MagicMock(),
            timeout_seconds=10,
            task_input_override=None,
            run_container_task_fn=fake_run,
            parse_rejected_tool_attempts_fn=lambda log: [],
            max_retry_attempts=2,
            attempt_label="initial",
        )
        # The loop stops as soon as the retried run's log no longer triggers a
        # retry, so exactly one retry runs here — and it gets its own label.
        assert labels == ["retry:json_parse_failure"]
        assert result[-1] is not None

    def test_review_nudge_uses_its_own_attempt_label(self, tmp_path):
        from unittest.mock import MagicMock

        from nichebench.core.datamodel import TestCaseSpec as Spec
        from nichebench.execution.runtime.executor import review_nudge as review

        captured: Dict[str, Any] = {}

        def fake_with_retry(**kwargs: Any):
            captured.update(kwargs)
            return ("o", "i", "l", {}, "img", None, None)

        mixin = MagicMock()
        mixin._run_container_runtime_task_with_retry = fake_with_retry
        review.run_review_nudge_pass(
            mixin=mixin,
            test_case=Spec(id="t", type="runtime", raw={}),
            workspace=MagicMock(path=tmp_path),
            runtime_config={},
            profile=MagicMock(),
            runtime_timeout_seconds=60,
            review_nudge="NUDGE",
        )
        assert captured["attempt_label"] == "review_nudge"
        assert captured["task_input_override"] == "NUDGE"


class _FakeProxy:
    """Stand-in for the live proxy handle."""

    def __init__(self) -> None:
        self.base_url = "http://127.0.0.1:1/nbcap-test/v1"
        self.port = 1
        self.capability_path = "/nbcap-test"
        self.token = "fake-capability-token"
        self.provider_key = "minimax"
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _capture_env(fake_bin: Path, executor: TestExecutor, tmp_path: Path) -> Any:
    """Compose the fake PATH with a stubbed capture proxy.

    Returns a single context manager so each test reads as one ``with``.
    """
    return _CombinedEnv(fake_bin, executor, tmp_path)


class _CombinedEnv:
    def __init__(self, fake_bin: Path, executor: TestExecutor, tmp_path: Path) -> None:
        self._env = patch.dict(
            os.environ,
            {
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                # Tells the fake docker stub to materialise the session store the
                # real runtime would have written, so the watchdog can poll it.
                "NB_FAKE_DOCKER_CREATE_DB": "1",
            },
        )
        self._proxy = _patch_proxy(executor, tmp_path)

    def __enter__(self) -> _FakeProxy:
        self._env.start()
        return self._proxy.__enter__()

    def __exit__(self, *exc: Any) -> None:
        self._proxy.__exit__(*exc)
        self._env.stop()


class _patch_proxy:
    """Patch ``CaptureSession.start_proxy`` to return a fake handle."""

    def __init__(self, executor: TestExecutor, tmp_path: Path) -> None:
        self.executor = executor
        self.tmp_path = tmp_path
        self.proxy = _FakeProxy()
        self._patches: List[Any] = []

    def __enter__(self) -> _FakeProxy:
        handle = self.proxy

        def _start(session: CaptureSession, journal: Any, upstream: str, **kwargs: Any) -> _FakeProxy:
            # Mirror the real method's bookkeeping: the session must hold the
            # handle so its own teardown can stop it.
            session._proxy = cast(Any, handle)
            return handle

        self._patches.append(
            patch.object(
                CaptureSession,
                "start_proxy",
                autospec=True,
                side_effect=_start,
            )
        )
        self._patches.append(patch.object(CaptureSession, "probe_health", return_value={"probed": False}))
        self._patches.append(patch.object(CaptureSession, "capture_identity", return_value={"image": IMAGE}))
        self._patches.append(patch.object(self.executor, "_get_provider_api_keys", return_value={}))
        for item in self._patches:
            item.start()
        return self.proxy

    def __exit__(self, *exc: Any) -> None:
        for item in reversed(self._patches):
            item.stop()


def interrupt_once():
    """Return a sleep replacement that raises ``KeyboardInterrupt`` once.

    Only the first call interrupts: the teardown path itself sleeps while
    draining the child, and that work must still run.
    """
    state = {"raised": False}
    real_sleep = time.sleep

    def _sleep(seconds: float) -> None:
        if not state["raised"]:
            state["raised"] = True
            raise KeyboardInterrupt()
        real_sleep(seconds)

    return _sleep


def sqlite_error() -> type:
    """Exception type raised by the patched DB poll."""
    import sqlite3

    return sqlite3.OperationalError
