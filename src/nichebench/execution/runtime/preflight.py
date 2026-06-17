"""Runtime preflight checks.

This module owns **host-side** and **workspace-side** preflight validation for
runtime task execution. It is invoked by the execution layer before the agent
runner is launched.

**What this module owns**
- Host-side validation of the container image reference (when mode is ``cage``)
  and ``ddev`` availability (when mode is ``host``).
- Workspace-side smoke-check script execution and result parsing.

**What this module does NOT own**
- DDEV project creation, startup, or teardown — those are owned by the runtime
  environment manager.
- Cage container lifecycle — that is owned by the cage runner.
- Check definitions or scoring logic — those live in the task manifest and
  scoring module.

**Callers**
- The execution layer calls ``run_runtime_preflight_host`` early, before any
  workspace or container is provisioned.
- The execution layer calls ``run_runtime_preflight_workspace`` after the
  workspace is created and the DDEV environment is fully started.

**Operational constraints**
- ``run_runtime_preflight_host`` is side-effect-free (no side effects beyond a
  version probe of ``docker`` or ``ddev``).
- ``run_runtime_preflight_workspace`` runs a smoke-check script in the host
  process; the script must be trusted because it executes with host privileges.
- Both functions raise ``validation_error_cls`` on failure — they do not
  return error codes or optional results.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any


def run_runtime_preflight_host(
    runtime_config: dict[str, Any],
    runtime_mode: str,
    subprocess_module: Any,
    validation_error_cls: type[Exception],
) -> None:
    """Run host-side preflight checks for runtime execution.

    Validates that the host environment satisfies the prerequisites for the
    requested ``runtime_mode`` before any workspace or container is provisioned.

    **Mode resolution**: ``runtime_mode`` values ``"cage"`` and ``"container"``
    are both treated as ``"cage"`` for validation purposes.

    **``cage`` mode checks**
    - ``runtime_container_image`` must be present and non-empty.
    - The image reference must not end with ``:latest``.
    - The image reference must contain both a registry/repository separator
      (``/``) and a tag separator (``:``) — i.e. it must be a fully pinned
      reference.  This guards against accidentally pulling an unversioned
      image.
    - ``docker --version`` must succeed on the host (soft check; no error is
      raised if it fails).

    **``host`` mode checks**
    - ``ddev --version`` must succeed on the host (soft check; no error is
      raised if it fails).

    Args:
        runtime_config: The runtime configuration dict. Required keys depend on
            ``runtime_mode`` — see the checks above.
        runtime_mode: One of ``"cage"``, ``"container"``, or ``"host"``.
        subprocess_module: The ``subprocess`` module (or a compatible mock for
            testing).  Used to probe tool availability without importing
            ``subprocess`` at module level.
        validation_error_cls: Exception class to raise on validation failure.
            Must accept a single string message.

    Raises:
        validation_error_cls: If a required configuration value is missing or
            invalid (cage mode), or if the image tag is floating.
    """
    effective_mode = "cage" if runtime_mode in ("cage", "container") else runtime_mode

    if effective_mode == "cage":
        image = str(runtime_config.get("runtime_container_image", "")).strip()
        if not image:
            raise validation_error_cls("runtime_container_image must be configured")
        if ":latest" in image:
            raise validation_error_cls("floating tag :latest is not allowed - use a pinned image reference")
        if "/" not in image or ":" not in image:
            raise validation_error_cls("Container image must be a pinned reference (not :latest or untagged)")

        with contextlib.suppress(subprocess_module.CalledProcessError, FileNotFoundError):
            subprocess_module.run(["docker", "--version"], capture_output=True, check=True)
    elif effective_mode == "host":
        with contextlib.suppress(subprocess_module.CalledProcessError, FileNotFoundError):
            subprocess_module.run(["ddev", "--version"], capture_output=True, check=True)


def run_runtime_preflight_workspace(
    workspace_path: Path,
    evaluation_config: dict[str, Any],
    subprocess_module: Any,
    sys_executable: str,
    script_path: Path,
    validation_error_cls: type[Exception],
) -> None:
    """Run workspace-side runtime preflight checks.

    Validates that the provisioned workspace and runtime environment are healthy
    by executing a smoke-check script.  This function is called after
    ``run_runtime_preflight_host`` and after the DDEV environment is fully
    started.

    **Workspace existence check**
    The workspace directory must already exist.  If it does not, a
    ``validation_error_cls`` is raised immediately.

    **Smoke preflight**
    If ``evaluation_config["runtime_smoke_preflight_enabled"]`` is ``False``,
    this function returns early without running any checks.

    Otherwise the script at ``script_path`` is executed with:

        sys_executable script_path --workspace workspace_path --json

    The script must exit ``0`` on success and emit a JSON object of the form:

        {
          "total": <int>,
          "failed": <int>,
          "checks": [{"name": <str>, "passed": <bool>, ...}, ...]
        }

    Any non-zero exit code, JSON decode failure, or failed check list causes a
    ``validation_error_cls`` to be raised with a summary of the failures.

    **Timeout**
    The script is subject to a timeout controlled by
    ``evaluation_config["runtime_smoke_preflight_timeout_seconds"]`` (default:
    180 seconds).  A ``TimeoutExpired`` exception from ``subprocess_module`` is
    wrapped as ``validation_error_cls``.

    Args:
        workspace_path: Absolute path to the run-specific workspace directory.
        evaluation_config: The evaluation configuration dict.  Controls whether
            smoke preflight runs and what timeout is applied.
        subprocess_module: The ``subprocess`` module (or a compatible mock).
        sys_executable: The Python interpreter to invoke the script with.
            Typically ``sys.executable``.
        script_path: Path to the smoke-preflight entry-point script.
        validation_error_cls: Exception class to raise on validation failure.

    Raises:
        validation_error_cls: If the workspace does not exist, if the smoke
            script times out, or if the script reports any failed checks.
    """
    if not workspace_path.exists():
        raise validation_error_cls(f"Workspace path does not exist: {workspace_path}")
    if not evaluation_config.get("runtime_smoke_preflight_enabled", False):
        return
    if not script_path.exists():
        raise validation_error_cls(f"Runtime smoke preflight script not found: {script_path}")

    timeout = int(evaluation_config.get("runtime_smoke_preflight_timeout_seconds", 180))
    try:
        proc = subprocess_module.run(
            [sys_executable, str(script_path), "--workspace", str(workspace_path), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess_module.TimeoutExpired:
        raise validation_error_cls(f"Smoke preflight timed out after {timeout}s")

    if proc.returncode == 0:
        return

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        tail = (proc.stderr or proc.stdout or "")[-500:].strip()
        raise validation_error_cls(f"Smoke preflight failed (exit {proc.returncode}): {tail}")

    failed = [c.get("name", "") for c in data.get("checks", []) if not c.get("passed")]
    failed_names = ", ".join(f for f in failed if f) or "(unknown)"
    raise validation_error_cls(
        f"Smoke preflight failed: {data.get('failed', 0)}/{data.get('total', 0)} checks failed: {failed_names}"
    )
