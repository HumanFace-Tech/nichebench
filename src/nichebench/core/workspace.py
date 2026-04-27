import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional


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
                subprocess.run(["git", "checkout", sha], cwd=self.path, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise WorkspaceError(f"Failed to create workspace: {exc.stderr}") from exc

        # Create artifact dirs after clone so git clone doesn't see a pre-existing destination
        self.run_artifacts_path.mkdir(parents=True, exist_ok=True)
        self._ensure_preconfigured_ddev_project_name()

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
        delete_result = self._run_cleanup_command(["ddev", "delete", "--omit-snapshot", "-y"], timeout=timeout)
        if delete_result.returncode != 0:
            self.command_log.append(
                {
                    "command": "ddev delete --omit-snapshot -y",
                    "warning": "Primary DDEV teardown failed; running fallback cleanup",
                    "returncode": delete_result.returncode,
                    "stdout": delete_result.stdout,
                    "stderr": delete_result.stderr,
                }
            )
            for cmd in (["ddev", "stop", "-y"],):
                self._run_cleanup_command(cmd, timeout=timeout)

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

    def _run_cleanup_command(
        self, command: list[str], timeout: Optional[int] = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a cleanup command and log without raising on failure."""
        try:
            result = subprocess.run(
                command,
                cwd=self.path,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            entry: dict[str, object] = {
                "command": " ".join(command),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            if result.returncode != 0:
                entry["warning"] = "Cleanup command failed"
            self.command_log.append(entry)
            return result
        except Exception as exc:
            entry = {
                "command": " ".join(command),
                "warning": "Cleanup command raised exception",
                "error": str(exc),
            }
            self.command_log.append(entry)
            return subprocess.CompletedProcess(command, 1, stdout="", stderr=str(exc))

    def ddev_start(
        self,
        setup_mode: str = "config_import",
        timeout: Optional[int] = None,
        post_setup_commands: Optional[list] = None,
    ):
        """Start DDEV and apply configured setup mode."""
        try:
            # Best-effort network hygiene before starting a new DDEV project.
            self._best_effort_network_hygiene(timeout=timeout)
            try:
                self._run_logged_command(["ddev", "start"], timeout=timeout)
            except subprocess.CalledProcessError as exc:
                if self._is_network_pool_exhaustion_error(exc):
                    self._best_effort_network_hygiene(timeout=timeout)
                    self._run_logged_command(["ddev", "start"], timeout=timeout)
                else:
                    raise
            self._run_logged_command(["ddev", "composer", "install"], timeout=timeout)

            if setup_mode == "config_import":
                self._run_logged_command(["ddev", "drush", "site:install", "minimal", "--yes"], timeout=timeout)
                # Point config sync dir at config/sync/ (git-tracked) so that
                # `ddev drush cex --yes` writes to the right place and
                # drush config:status compares against the correct directory.
                self._patch_settings_php()
                # Sync site UUID from config/sync/system.site.yml so cim doesn't reject it
                site_yml = self.path / "config" / "sync" / "system.site.yml"
                if site_yml.exists():
                    import yaml  # local import — pyyaml is a transitive dep

                    site_data = yaml.safe_load(site_yml.read_text())
                    site_uuid = site_data.get("uuid", "")
                    if site_uuid:
                        self._run_logged_command(
                            ["ddev", "drush", "config:set", "system.site", "uuid", site_uuid, "--yes"],
                            timeout=timeout,
                        )
                self._run_logged_command(
                    ["ddev", "drush", "cim", "--yes", "--source=/var/www/html/config/sync"],
                    timeout=timeout,
                )
                # Seed fixtures — site-specific command, ignore failure gracefully
                try:
                    result = subprocess.run(
                        ["ddev", "drush", "nichejobs:seed"],
                        cwd=self.path,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                    )
                    log_entry: dict[str, object] = {
                        "command": "ddev drush nichejobs:seed",
                        "returncode": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                    if result.returncode != 0:
                        stderr_or_stdout = result.stderr.strip() or result.stdout.strip()
                        warning = f"nichejobs:seed failed (rc={result.returncode}): {stderr_or_stdout}"
                        log_entry["warning"] = warning
                        self.setup_warnings.append(warning)
                    self.command_log.append(log_entry)
                except subprocess.TimeoutExpired as exc:
                    warning = f"nichejobs:seed timed out after {timeout}s"
                    self.setup_warnings.append(warning)
                    self.command_log.append(
                        {"command": "ddev drush nichejobs:seed", "warning": warning, "error": str(exc)}
                    )
                except Exception as exc:
                    warning = f"nichejobs:seed raised unexpected exception: {exc}"
                    self.setup_warnings.append(warning)
                    self.command_log.append(
                        {"command": "ddev drush nichejobs:seed", "warning": warning, "error": str(exc)}
                    )

            elif setup_mode == "db_snapshot":
                snapshot_gz = self.path / ".ddev" / "db_snapshots" / f"{self.task_id}.sql.gz"
                snapshot_sql = self.path / "db.sql"
                if snapshot_gz.exists():
                    self._run_logged_command(["ddev", "import-db", f"--file={snapshot_gz}"], timeout=timeout)
                elif snapshot_sql.exists():
                    self._run_logged_command(["ddev", "import-db", f"--file={snapshot_sql}"], timeout=timeout)

            if post_setup_commands:
                for cmd in post_setup_commands:
                    self._run_logged_command(cmd, timeout=timeout)

            self._ddev_health_check(timeout=timeout)
        except subprocess.CalledProcessError as exc:
            raise DDEVError(f"DDEV command failed: {exc.stderr}") from exc

    def _best_effort_network_hygiene(self, timeout: Optional[int] = None) -> None:
        """Reduce docker network pressure without raising."""
        try:
            self._cleanup_stale_harness_containers(timeout=timeout)
        except Exception as exc:
            self.command_log.append(
                {
                    "command": "docker rm -f <stale harness containers>",
                    "warning": "Stale harness container cleanup failed",
                    "error": str(exc),
                }
            )

        try:
            self._cleanup_stale_harness_networks(timeout=timeout)
        except Exception as exc:
            self.command_log.append(
                {
                    "command": "docker network rm <stale harness networks>",
                    "warning": "Stale harness network cleanup failed",
                    "error": str(exc),
                }
            )

    def _cleanup_stale_harness_containers(self, timeout: Optional[int] = None) -> None:
        """Remove stale `ddev-nb-*` containers from previous harness runs."""
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            cwd=self.path,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        current_prefix = f"ddev-{self.ddev_project_name}-"
        container_names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        stale_containers = [
            name for name in container_names if name.startswith("ddev-nb-") and not name.startswith(current_prefix)
        ]
        for container_name in stale_containers:
            rm_result = subprocess.run(
                ["docker", "rm", "-f", container_name],
                cwd=self.path,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            self.command_log.append(
                {
                    "command": f"docker rm -f {container_name}",
                    "returncode": rm_result.returncode,
                    "stdout": rm_result.stdout,
                    "stderr": rm_result.stderr,
                }
            )

        try:
            self._run_docker_network_prune(timeout=timeout)
        except Exception as exc:
            self.command_log.append(
                {
                    "command": "docker network prune -f",
                    "warning": "Docker network prune failed",
                    "error": str(exc),
                }
            )

    def _cleanup_stale_harness_networks(self, timeout: Optional[int] = None) -> None:
        """Remove stopped stale `ddev-nb-*` networks from previous harness runs."""
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            cwd=self.path,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        current_default = f"ddev-{self.ddev_project_name}_default"
        network_names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        stale_networks = [
            name
            for name in network_names
            if name.startswith("ddev-nb-") and name.endswith("_default") and name != current_default
        ]
        for network_name in stale_networks:
            rm_result = subprocess.run(
                ["docker", "network", "rm", network_name],
                cwd=self.path,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            self.command_log.append(
                {
                    "command": f"docker network rm {network_name}",
                    "returncode": rm_result.returncode,
                    "stdout": rm_result.stdout,
                    "stderr": rm_result.stderr,
                }
            )

    @staticmethod
    def _is_network_pool_exhaustion_error(exc: subprocess.CalledProcessError) -> bool:
        """Return True when Docker subnet pools are exhausted."""
        haystack = f"{getattr(exc, 'stdout', '')}\n{getattr(exc, 'stderr', '')}".lower()
        return "all predefined address pools have been fully subnetted" in haystack

    def _run_docker_network_prune(self, timeout: Optional[int] = None) -> None:
        """Prune unused docker networks and record command output."""
        result = subprocess.run(
            ["docker", "network", "prune", "-f"],
            cwd=self.path,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        self.command_log.append(
            {
                "command": "docker network prune -f",
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )

    def ddev_stop(self, timeout: Optional[int] = None):
        """Stop, remove data, and unlist DDEV instance."""
        try:
            self._run_logged_command(["ddev", "delete", "--omit-snapshot", "-y"], timeout=timeout)
        except subprocess.CalledProcessError as exc:
            raise DDEVError(f"DDEV stop failed: {exc.stderr}") from exc

    def _ddev_health_check(self, timeout: Optional[int] = None):
        """Verify DDEV instance health."""
        try:
            self._run_logged_command(["ddev", "describe"], timeout=timeout)
        except subprocess.CalledProcessError as exc:
            raise DDEVError(f"DDEV health check failed: {exc.stderr}") from exc

    def run_ddev_drush(self, args: list[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess[str]:
        """Run a drush command through DDEV and record it."""
        return self._run_logged_command(["ddev", "drush", *args], timeout=timeout)

    def capture_diff(self) -> str:
        """Capture current git diff for workspace artifacts."""
        try:
            result = subprocess.run(["git", "diff", "HEAD"], cwd=self.path, check=True, capture_output=True, text=True)
            return result.stdout
        except subprocess.CalledProcessError as exc:
            raise WorkspaceError(f"Failed to capture diff: {exc.stderr}") from exc

    def capture_final_diff(self, base_sha: Optional[str]) -> str:
        """Capture baseline-to-final diff for runtime artifacts.

        Stages all working-tree changes before diffing so that new files written
        by the agent (which are untracked in git) are included in the output.
        """
        try:
            # Stage task-relevant changes while excluding harness-generated files.
            add_cmd = [
                "git",
                "add",
                "-A",
                "--",
                ".",
                ":(exclude)TASK.md",
                ":(exclude)opencode.json",
                ":(exclude).nichebench-runtime-task.txt",
                ":(exclude).ddev/config.yaml",
                ":(exclude)results/run/**",
            ]
            subprocess.run(
                add_cmd,
                cwd=self.path,
                check=True,
                capture_output=True,
                text=True,
            )
            cmd = ["git", "diff", "--cached", base_sha] if base_sha else ["git", "diff", "--cached", "HEAD"]
            result = subprocess.run(
                cmd,
                cwd=self.path,
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as exc:
            raise WorkspaceError(f"Failed to capture final diff: {exc.stderr}") from exc

    def capture_git_log(self, base_sha: Optional[str]) -> str:
        """Capture commit history for runtime artifact bundles."""
        if not base_sha:
            base_sha = "HEAD~1"

        try:
            result = subprocess.run(
                ["git", "log", "--oneline", f"{base_sha}...HEAD"],
                cwd=self.path,
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as exc:
            raise WorkspaceError(f"Failed to capture git log: {exc.stderr}") from exc

    def _run_logged_command(
        self, command: list[str], timeout: Optional[int] = None
    ) -> subprocess.CompletedProcess[str]:
        """Run command in workspace and append details to command log."""
        result = subprocess.run(command, cwd=self.path, check=True, capture_output=True, text=True, timeout=timeout)
        self.command_log.append(
            {
                "command": " ".join(command),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        return result

    def _patch_settings_php(self) -> None:
        """Append config_sync_directory to settings.php after site:install.

        Drupal's default falls back to sites/default/files/sync when
        $settings['config_sync_directory'] is unset, but the runtime pack
        keeps config in config/sync/ (git-tracked).  Patching here ensures
        `ddev drush cex --yes` writes there and `drush config:status` compares
        against the correct directory.

        settings.php is gitignored so this patch is invisible to final.diff.
        """
        settings_php = self.path / "web" / "sites" / "default" / "settings.php"
        if not settings_php.exists():
            return
        content = settings_php.read_text()
        marker = "# [nichebench] config_sync_directory"
        if marker in content:
            return  # already patched
        patch = f"\n{marker}\n" "$settings['config_sync_directory'] = '../config/sync';\n"
        settings_php.write_text(content + patch)
        self.command_log.append(
            {
                "command": "patch settings.php: config_sync_directory → config/sync",
                "returncode": 0,
                "stdout": "Patched settings.php",
                "stderr": "",
            }
        )

    def patch_static_analysis_configs(self) -> Optional[str]:
        """Patch phpstan.neon and composer.json before the agent runs.

        phpstan.neon: removes the ``includes:`` block whose entries are already
        auto-loaded by phpstan/extension-installer, preventing the
        "files included multiple times" fatal error.

        composer.json: removes the hardcoded ``web/modules/custom`` path from
        the ``cs`` (phpcs) script so the path argument supplied by check specs
        is the only target, avoiding false failures from pre-existing violations
        in other modules.

        Both patches are committed so they don't appear in final.diff (which
        is diffed against the returned SHA, not the original resolved_sha).

        Returns the new HEAD SHA after committing, or None if no patches were
        applied or the commit failed.
        """
        patches: dict[str, str] = {}

        # 1. phpstan.neon — remove manual includes (extension-installer handles them),
        #    remove deprecated drupal.drupal_root parameter, and narrow paths to
        #    only the agent module so pre-existing nichejobs_core violations don't
        #    contaminate the check.
        phpstan_neon = self.path / "phpstan.neon"
        if phpstan_neon.exists():
            content = phpstan_neon.read_text()
            # Remove the includes: block
            new_content = re.sub(
                r"^includes:\n(?:  - [^\n]+\n)+\n?",
                "",
                content,
                flags=re.MULTILINE,
            )
            # Remove deprecated drupal: subsection (drupal_root is auto-discovered)
            new_content = re.sub(
                r"\n  drupal:\n(?:    [^\n]+\n)+",
                "\n",
                new_content,
            )
            # Narrow paths: from all of web/modules/custom to just the agent module
            new_content = re.sub(
                r"(    - )web/modules/custom\n",
                r"\1web/modules/custom/nichejobs_application\n",
                new_content,
            )
            if new_content != content:
                patches["phpstan.neon"] = new_content

        # 2. composer.json — drop hardcoded scan path from 'cs' and 'cs-fix' scripts
        #    so the check runner and the agent can supply a targeted path.
        composer_json_path = self.path / "composer.json"
        if composer_json_path.exists():
            content = composer_json_path.read_text()
            # Targeted substitution preserves original JSON formatting
            new_content = re.sub(
                r'("cs":\s*"phpcs [^"]*?) web/modules/custom(")',
                r"\1\2",
                content,
            )
            new_content = re.sub(
                r'("cs-fix":\s*"phpcbf [^"]*?) web/modules/custom(")',
                r"\1\2",
                new_content,
            )
            if new_content != content:
                patches["composer.json"] = new_content

        if not patches:
            return None

        for rel_path, new_content in patches.items():
            (self.path / rel_path).write_text(new_content)

        try:
            subprocess.run(
                ["git", "add"] + list(patches.keys()),
                cwd=self.path,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=nichebench",
                    "-c",
                    "user.email=bench@local",
                    "commit",
                    "-m",
                    "harness: fix static analysis configs for isolated check runs",
                ],
                cwd=self.path,
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.path,
                check=True,
                capture_output=True,
                text=True,
            )
            new_sha = result.stdout.strip()
            self.command_log.append(
                {
                    "command": "patch_static_analysis_configs",
                    "returncode": 0,
                    "stdout": (f"Committed patches {list(patches.keys())} → {new_sha}"),
                    "stderr": "",
                }
            )
            return new_sha
        except subprocess.CalledProcessError as exc:
            self.command_log.append(
                {
                    "command": "patch_static_analysis_configs",
                    "returncode": getattr(exc, "returncode", 1),
                    "stdout": getattr(exc, "stdout", ""),
                    "stderr": getattr(exc, "stderr", str(exc)),
                }
            )
            return None
