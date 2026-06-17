"""Workspace model and exceptions for runtime workspace lifecycle.

Ownership
--------
This module is the public facade for the workspace package.  It exposes
``Workspace``, ``WorkspaceError``, and ``DDEVError`` as the primary API.
All other modules (``cleanup.py``, ``ddev.py``, ``static_analysis.py``,
``diff.py``) are internal implementation details.

Workspace lifecycle
-------------------
1. ``create()`` — clone source repo, checkout task SHA, create result dirs.
2. ``ddev_start()`` — launch DDEV, apply setup_mode (config_import or db_snapshot).
3. ``capture_*()`` — collect diffs, logs, and git history for the artifact bundle.
4. ``cleanup()`` — tear down DDEV (primary + fallback) and remove workspace dir.

Side-effect boundaries
---------------------
- Patches settings.php to set ``config_sync_directory`` — this patch is gitignored
  so it does not appear in ``final.diff``.
- ``patch_static_analysis_configs()`` commits its patches to a private branch
  so they are excluded from ``final.diff`` (diffed against HEAD after commit).
- Does NOT own trajectory building (see ``trajectory.py``)
- Does NOT own artifact persistence (see ``artifacts.py``)
"""

import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from nichebench.execution.runtime.workspace import cleanup as _cleanup
from nichebench.execution.runtime.workspace import ddev as _ddev
from nichebench.execution.runtime.workspace import diff as _diff
from nichebench.execution.runtime.workspace import static_analysis as _static_analysis


class WorkspaceError(Exception):
    """Exception raised for workspace-related errors."""


class DDEVError(Exception):
    """Exception raised for DDEV operation errors."""


class Workspace:
    """Manage isolated runtime workspace lifecycle and command logging."""

    def __init__(self, base_path: Path, task_id: str):
        self.base_path = base_path
        self.task_id = task_id
        self.workspace_id = str(uuid.uuid4())[:8]
        self.path = base_path / f"run-{task_id}-{self.workspace_id}"
        safe_task_id = "".join(c if c.isalnum() else "-" for c in task_id).strip("-").lower()[:24] or "task"
        self.ddev_project_name = f"nb-{safe_task_id}-{self.workspace_id}"
        self.run_artifacts_path = self.path / "results" / "run"
        self.command_log: list[dict[str, object]] = []
        self.setup_warnings: list[str] = []

    def create(self, source_path: Path, sha: Optional[str] = None):
        """Create a new isolated workspace from source repository path."""
        if self.path.exists():
            try:
                shutil.rmtree(self.path)
            except Exception as exc:
                self.command_log.append(
                    {
                        "command": f"rmtree {self.path}",
                        "warning": "Workspace directory removal failed",
                        "error": str(exc),
                    }
                )

        self.base_path.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.run(
                ["git", "clone", str(source_path), str(self.path)],
                check=True,
                capture_output=True,
                text=True,
            )
            if sha:
                subprocess.run(
                    ["git", "checkout", sha],
                    cwd=self.path,
                    check=True,
                    capture_output=True,
                    text=True,
                )
        except subprocess.CalledProcessError as exc:
            raise WorkspaceError(f"Failed to create workspace: {exc.stderr}") from exc

        # Create artifact dirs after clone so git clone doesn't see a pre-existing destination
        self.run_artifacts_path.mkdir(parents=True, exist_ok=True)
        self._ensure_preconfigured_ddev_project_name()
        self._ensure_agents_md()

    def _ensure_agents_md(self) -> None:
        """Copy AGENTS.mut.md to AGENTS.md in workspace if AGENTS.md is absent.

        Guarantees agent guidance availability: when the task branch ships
        AGENTS.mut.md but not AGENTS.md, the MUT agent can still discover it
        under the canonical filename.  No-ops when AGENTS.md already exists.
        """
        agents_md = self.path / "AGENTS.md"
        agents_mut_md = self.path / "AGENTS.mut.md"
        if not agents_md.exists() and agents_mut_md.exists():
            shutil.copy2(str(agents_mut_md), str(agents_md))

    def _ensure_preconfigured_ddev_project_name(self) -> None:
        """Pin DDEV project name in committed config without running `ddev config`."""
        config_path = self.path / ".ddev" / "config.yaml"
        if not config_path.exists():
            return

        raw = config_path.read_text(encoding="utf-8")
        replacement = f"name: {self.ddev_project_name}"
        if re.search(r"^name:\s*.*$", raw, flags=re.MULTILINE):
            updated = re.sub(r"^name:\s*.*$", replacement, raw, count=1, flags=re.MULTILINE)
        else:
            updated = f"{replacement}\n{raw}"

        if updated != raw:
            config_path.write_text(updated, encoding="utf-8")

    def cleanup(self, timeout: Optional[int] = None, remove_workspace: bool = True):
        """Clean up DDEV runtime resources and optionally remove workspace files."""
        # Pass the project name explicitly so the command works regardless of
        # CWD validity.  DDEV accepts `ddev delete <projectname>` from any
        # directory, which avoids failures when the workspace path has issues.
        delete_result = _cleanup.run_cleanup_command(
            ["ddev", "delete", "--omit-snapshot", "-y", self.ddev_project_name],
            path=self.path,
            command_log=self.command_log,
            timeout=timeout,
        )
        if delete_result.returncode != 0:
            self.command_log.append(
                {
                    "command": f"ddev delete --omit-snapshot -y {self.ddev_project_name}",
                    "warning": "Primary DDEV teardown failed; running fallback cleanup",
                    "returncode": delete_result.returncode,
                    "stdout": delete_result.stdout,
                    "stderr": delete_result.stderr,
                }
            )
            # Fallback: stop with --remove-data fully deletes containers,
            # volumes, and the project registration.  Plain `ddev stop`
            # only pauses the project and leaves it in DDEV's registry.
            _cleanup.run_cleanup_command(
                ["ddev", "stop", "--remove-data", "-y", self.ddev_project_name],
                path=self.path,
                command_log=self.command_log,
                timeout=timeout,
            )

        if remove_workspace and self.path.exists():
            try:
                shutil.rmtree(self.path)
            except Exception as exc:
                self.command_log.append(
                    {
                        "command": f"rmtree {self.path}",
                        "warning": "Workspace directory removal failed",
                        "error": str(exc),
                    }
                )

    def ddev_start(
        self,
        setup_mode: str = "config_import",
        timeout: Optional[int] = None,
        post_setup_commands: Optional[list] = None,
    ):
        """Start DDEV and apply configured setup mode."""
        _ddev.ddev_start(
            path=self.path,
            setup_mode=setup_mode,
            command_log=self.command_log,
            setup_warnings=self.setup_warnings,
            timeout=timeout,
            post_setup_commands=post_setup_commands,
        )

    def ddev_stop(self, timeout: Optional[int] = None):
        """Stop, remove data, and unlist DDEV instance."""
        _ddev.ddev_stop(
            path=self.path,
            command_log=self.command_log,
            timeout=timeout,
        )

    def run_ddev_drush(self, args: list[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess[str]:
        """Run a drush command through DDEV and record it."""
        return _ddev.run_ddev_drush(
            args=args,
            path=self.path,
            command_log=self.command_log,
            timeout=timeout,
        )

    def _run_logged_command(self, cmd: list[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess[str]:
        """Run a workspace command and append the result to ``command_log``.

        This private method is retained as part of the ``Workspace`` facade
        because runtime orchestration and tests call it directly for setup
        probes that are not Drupal/Drush-specific.
        """
        return _ddev.run_logged_command(
            command=cmd,
            path=self.path,
            command_log=self.command_log,
            timeout=timeout,
        )

    def capture_diff(self) -> str:
        """Capture current git diff for workspace artifacts."""
        return _diff.capture_diff(path=self.path)

    def capture_final_diff(self, base_sha: Optional[str]) -> str:
        """Capture baseline-to-final diff for runtime artifacts."""
        return _diff.capture_final_diff(
            path=self.path,
            base_sha=base_sha,
            command_log=self.command_log,
        )

    def capture_git_log(self, base_sha: Optional[str]) -> str:
        """Capture commit history for runtime artifact bundles."""
        return _diff.capture_git_log(
            path=self.path,
            base_sha=base_sha,
            command_log=self.command_log,
        )

    def patch_static_analysis_configs(self) -> Optional[str]:
        """Patch phpstan.neon and composer.json before the agent runs."""
        return _static_analysis.patch_static_analysis_configs(
            path=self.path,
            command_log=self.command_log,
        )

    @staticmethod
    def _is_network_pool_exhaustion_error(exc: subprocess.CalledProcessError) -> bool:
        """Return True when Docker subnet pools are exhausted."""
        return _ddev.is_network_pool_exhaustion_error(exc)
