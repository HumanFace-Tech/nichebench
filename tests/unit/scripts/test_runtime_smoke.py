"""Unit tests for scripts/runtime_smoke.py.

These tests do NOT require Docker, DDEV, or a real Drupal installation.
``subprocess.run`` is mocked so each scenario is reproducible.

Coverage:
- ``run_check`` returns ``passed=True`` only when exit code is 0.
- Checks with ``success_marker`` require the marker substring in stdout
  even when exit code is 0 (Drush ``config:status`` drift false-positive).
- Failures (non-zero exit, timeout, OSError) produce a structured failure
  record with a useful ``failure_reason``.
- The CLI emits valid JSON with ``--json`` and exits 0 only when all checks
  pass.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
RUNTIME_SMOKE_PATH = SCRIPTS_DIR / "runtime_smoke.py"


@pytest.fixture(scope="module")
def runtime_smoke():
    """Import scripts/runtime_smoke.py from its absolute path.

    The script lives outside the ``nichebench`` package on purpose; loading
    it via ``importlib`` keeps tests independent of ``sys.path`` mutations.
    """
    spec = importlib.util.spec_from_file_location("runtime_smoke", RUNTIME_SMOKE_PATH)
    assert spec and spec.loader, "runtime_smoke.py must exist and be importable"
    module = importlib.util.module_from_spec(spec)
    sys.modules["runtime_smoke"] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("runtime_smoke", None)


def _completed_process(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    """Build a mock ``subprocess.run`` return value."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# Static structure
# ---------------------------------------------------------------------------


def test_checks_list_is_non_empty(runtime_smoke):
    """Sanity: the script must define at least one check."""
    assert runtime_smoke.CHECKS, "CHECKS list must not be empty"
    assert all(isinstance(spec, runtime_smoke.CheckSpec) for spec in runtime_smoke.CHECKS)


def test_config_status_check_has_no_differences_marker(runtime_smoke):
    """The drush_config_status check must require the 'No differences' marker.

    This guards the regression where exit code 0 with drift in stdout was
    silently accepted as healthy.
    """
    cfg_check = next(spec for spec in runtime_smoke.CHECKS if spec.name == "drush_config_status")
    assert cfg_check.success_marker == "No differences"
    assert cfg_check.failure_hint, "failure_hint must explain how to remediate drift"


def test_no_check_uses_shell_pipes(runtime_smoke):
    """Subprocess command arrays must not contain shell pipes or redirections."""
    forbidden = {"|", ">", "<", ";", "&&", "||"}
    for spec in runtime_smoke.CHECKS:
        for token in spec.command:
            assert token not in forbidden, f"{spec.name}: token {token!r} is shell syntax — pass argv lists only"


# ---------------------------------------------------------------------------
# run_check — happy path
# ---------------------------------------------------------------------------


def test_run_check_passes_when_exit_zero_and_marker_present(runtime_smoke, tmp_path):
    spec = next(s for s in runtime_smoke.CHECKS if s.name == "drush_config_status")
    proc = _completed_process(returncode=0, stdout="[OK] No differences\n")

    with patch.object(runtime_smoke.subprocess, "run", return_value=proc) as mock_run:
        result = runtime_smoke.run_check(tmp_path, spec)

    assert result["passed"] is True
    assert result["returncode"] == 0
    assert result["failure_reason"] == ""
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs.get("cwd") == tmp_path
    assert call_kwargs.get("capture_output") is True
    assert call_kwargs.get("text") is True


def test_run_check_passes_for_check_without_marker_on_exit_zero(runtime_smoke, tmp_path):
    spec = next(s for s in runtime_smoke.CHECKS if s.name == "drush_sql_query")
    assert spec.success_marker is None, "precondition: no marker on this check"
    proc = _completed_process(returncode=0, stdout="1\n")

    with patch.object(runtime_smoke.subprocess, "run", return_value=proc):
        result = runtime_smoke.run_check(tmp_path, spec)

    assert result["passed"] is True


# ---------------------------------------------------------------------------
# run_check — drift false-positive (the bug being fixed)
# ---------------------------------------------------------------------------


def test_run_check_fails_on_exit_zero_with_drift_in_stdout(runtime_smoke, tmp_path):
    """Drush can exit 0 even when drift exists; marker check must catch this."""
    spec = next(s for s in runtime_smoke.CHECKS if s.name == "drush_config_status")
    drift_stdout = "Drupal\\user\\role\\role.fbb23cf64a role:create  Addition\n" " Name: test_role\n" " Weight: 0\n"
    proc = _completed_process(returncode=0, stdout=drift_stdout)

    with patch.object(runtime_smoke.subprocess, "run", return_value=proc):
        result = runtime_smoke.run_check(tmp_path, spec)

    assert result["passed"] is False
    assert "No differences" in result["failure_reason"]
    assert "drift" in result["failure_reason"]
    assert "config:status" in result["failure_reason"], "failure_reason must include the remediation hint"


def test_run_check_marker_is_substring_not_exact_match(runtime_smoke, tmp_path):
    """Marker match should be substring-based, tolerant of leading banners."""
    spec = next(s for s in runtime_smoke.CHECKS if s.name == "drush_config_status")
    proc = _completed_process(
        returncode=0,
        stdout="Collection   Config Operation   \nNo differences.\n",
    )
    with patch.object(runtime_smoke.subprocess, "run", return_value=proc):
        result = runtime_smoke.run_check(tmp_path, spec)
    assert result["passed"] is True


def test_run_check_marker_found_in_stderr(runtime_smoke, tmp_path):
    """Drush may route the 'No differences' notice to stderr — marker check must
    still recognise that as a clean state. This is the runtime-observed case:
    the DDEV environment emitted ``[notice] No differences between DB and sync
    directory.`` to stderr while leaving stdout empty.
    """
    spec = next(s for s in runtime_smoke.CHECKS if s.name == "drush_config_status")
    proc = _completed_process(
        returncode=0,
        stdout="",
        stderr=" [notice] No differences between DB and sync directory.\n",
    )
    with patch.object(runtime_smoke.subprocess, "run", return_value=proc):
        result = runtime_smoke.run_check(tmp_path, spec)
    assert result["passed"] is True
    assert result["failure_reason"] == ""


# ---------------------------------------------------------------------------
# run_check — failure paths
# ---------------------------------------------------------------------------


def test_run_check_fails_when_exit_code_nonzero(runtime_smoke, tmp_path):
    spec = runtime_smoke.CheckSpec(
        name="custom",
        command=["ddev", "drush", "status"],
        timeout=10,
        success_marker="No differences",
        failure_hint="see manual",
    )
    proc = _completed_process(returncode=2, stdout="", stderr="DB unreachable")

    with patch.object(runtime_smoke.subprocess, "run", return_value=proc):
        result = runtime_smoke.run_check(tmp_path, spec)

    assert result["passed"] is False
    assert result["returncode"] == 2
    assert "non-zero" in result["failure_reason"]


def test_run_check_records_timeout_as_failure(runtime_smoke, tmp_path):
    spec = runtime_smoke.CheckSpec(
        name="slow",
        command=["ddev", "drush", "status"],
        timeout=5,
    )
    with patch.object(
        runtime_smoke.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd=["ddev", "drush", "status"], timeout=5),
    ):
        result = runtime_smoke.run_check(tmp_path, spec)

    assert result["passed"] is False
    assert result["returncode"] is None
    assert "timed out" in result["failure_reason"]
    assert "timeout after 5s" in result["stderr"]


def test_run_check_records_oserror_as_failure(runtime_smoke, tmp_path):
    spec = runtime_smoke.CheckSpec(
        name="missing",
        command=["ddev", "status"],
        timeout=5,
    )
    with patch.object(runtime_smoke.subprocess, "run", side_effect=OSError("No such file")):
        result = runtime_smoke.run_check(tmp_path, spec)

    assert result["passed"] is False
    assert result["returncode"] is None
    assert "failed to launch" in result["failure_reason"]
    assert "No such file" in result["stderr"]


def test_run_check_does_not_use_shell_true(runtime_smoke, tmp_path):
    """Defence-in-depth: subprocess.run must never enable shell=True."""
    spec = runtime_smoke.CheckSpec(
        name="noop",
        command=["ddev", "status"],
        timeout=5,
    )
    with patch.object(runtime_smoke.subprocess, "run", return_value=_completed_process(returncode=0)) as mock_run:
        runtime_smoke.run_check(tmp_path, spec)
    assert mock_run.call_args.kwargs.get("shell", False) is False


# ---------------------------------------------------------------------------
# main() — CLI surface
# ---------------------------------------------------------------------------


def _fake_workspace(tmp_path: Path) -> Path:
    """Create a minimal DDEV project for ``main`` tests."""
    ws = tmp_path / "ws"
    (ws / ".ddev").mkdir(parents=True)
    (ws / ".ddev" / "config.yaml").write_text("name: test-project\n")
    return ws


def test_main_exits_2_when_workspace_missing(runtime_smoke, tmp_path, capsys):
    missing = tmp_path / "missing"
    with patch.object(runtime_smoke.subprocess, "run") as mock_run:
        rc = _invoke_main(
            runtime_smoke,
            ["--workspace", str(missing), "--json"],
        )

    assert rc == 2
    mock_run.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "workspace_not_found"
    assert "workspace not found" in payload["error"]["message"]


def test_main_exits_2_when_workspace_is_not_a_ddev_project(runtime_smoke, tmp_path, capsys):
    workspace = tmp_path / "not-ddev"
    workspace.mkdir()
    with patch.object(runtime_smoke.subprocess, "run") as mock_run:
        rc = _invoke_main(runtime_smoke, ["--workspace", str(workspace)])

    assert rc == 2
    mock_run.assert_not_called()
    output = capsys.readouterr().out
    assert "Error:" in output
    assert "not a DDEV project" in output
    assert str(workspace / ".ddev" / "config.yaml") in output


def test_main_exits_0_when_all_checks_pass(runtime_smoke, tmp_path, capsys):
    """Mock subprocess to return clean (exit 0 + 'No differences') for every check."""
    ws = _fake_workspace(tmp_path)
    proc_ok = _completed_process(returncode=0, stdout="No differences\n")
    with patch.object(runtime_smoke.subprocess, "run", return_value=proc_ok):
        rc = _invoke_main(runtime_smoke, ["--workspace", str(ws), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] == payload["total"]
    assert payload["failed"] == 0
    assert all(c["passed"] for c in payload["checks"])
    assert all(c["failure_reason"] == "" for c in payload["checks"])


def test_main_exits_1_when_drift_detected(runtime_smoke, tmp_path, capsys):
    """Drift with exit 0 must still cause the script to exit non-zero."""
    ws = _fake_workspace(tmp_path)
    proc_drift = _completed_process(
        returncode=0,
        stdout="Drupal\\user\\role\\role.foo role:create  Addition\n",
    )

    def side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("cmd", [])
        # Pretend config:status drifts but every other check passes.
        if "config:status" in cmd:
            return proc_drift
        return _completed_process(returncode=0, stdout="")

    with patch.object(runtime_smoke.subprocess, "run", side_effect=side_effect):
        rc = _invoke_main(runtime_smoke, ["--workspace", str(ws), "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] >= 1
    failed = [c for c in payload["checks"] if not c["passed"]]
    assert any(c["name"] == "drush_config_status" for c in failed)
    assert any("drift" in c["failure_reason"] for c in failed)


def test_main_exits_1_when_subprocess_fails(runtime_smoke, tmp_path, capsys):
    """A non-zero exit anywhere must propagate to a non-zero main() exit code."""
    ws = _fake_workspace(tmp_path)

    def side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("cmd", [])
        if "config:status" in cmd:
            return _completed_process(returncode=0, stdout="No differences\n")
        # The drush_status command is `ddev drush status --fields=...`.
        # Match by presence of "bootstrap,drupal-version" anywhere in argv.
        if any("bootstrap,drupal-version" in c for c in cmd):
            return _completed_process(returncode=1, stdout="", stderr="boom")
        return _completed_process(returncode=0, stdout="")

    with patch.object(runtime_smoke.subprocess, "run", side_effect=side_effect):
        rc = _invoke_main(runtime_smoke, ["--workspace", str(ws), "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    failed = [c for c in payload["checks"] if not c["passed"]]
    assert any(c["name"] == "drush_status" for c in failed)
    assert any("non-zero" in c["failure_reason"] for c in failed)


def test_main_text_output_contains_failure_reason_on_drift(runtime_smoke, tmp_path, capsys):
    ws = _fake_workspace(tmp_path)
    proc_drift = _completed_process(
        returncode=0,
        stdout="Drupal\\block\\block.bar block:create  Addition\n",
    )

    def side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("cmd", [])
        if "config:status" in cmd:
            return proc_drift
        return _completed_process(returncode=0, stdout="")

    with patch.object(runtime_smoke.subprocess, "run", side_effect=side_effect):
        rc = _invoke_main(runtime_smoke, ["--workspace", str(ws)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "drush_config_status" in out
    assert "drift" in out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_main(runtime_smoke, argv: list[str]) -> int:
    """Run runtime_smoke.main() with the given argv (and restore it after).

    Stdout/stderr are left untouched here — pytest's ``capsys`` fixture owns
    stdout capture so the test can assert on what main() printed.
    """
    saved = sys.argv
    sys.argv = ["runtime_smoke.py", *argv]
    try:
        try:
            return runtime_smoke.main()
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 0
    finally:
        sys.argv = saved
