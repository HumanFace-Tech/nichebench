"""DDEV operations for runtime workspace lifecycle.

Ownership
--------
This module is owned by the workspace package.  It contains DDEV-specific
operations that are called from ``Workspace`` methods.

Side-effect boundaries
---------------------
- Runs ``ddev start``, ``ddev stop``, ``ddev delete`` and health checks.
- Runs ``ddev composer install``, ``ddev drush`` commands.
- Patches ``settings.php`` for config_sync_directory.
- Logs all operations to the provided ``command_log`` list.
- Does NOT own workspace creation or cleanup (those are in ``model.py``).
"""

import subprocess
from pathlib import Path
from typing import Optional


def is_network_pool_exhaustion_error(exc: subprocess.CalledProcessError) -> bool:
    """Return True when Docker subnet pools are exhausted.

    Args:
        exc: A CalledProcessError from a ddev command.

    Returns:
        True if the error message indicates Docker subnet exhaustion.
    """
    haystack = f"{getattr(exc, 'stdout', '')}\n{getattr(exc, 'stderr', '')}".lower()
    return "all predefined address pools have been fully subnetted" in haystack


def run_logged_command(
    command: list[str],
    path: Path,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
    """Run command in workspace and append details to command log.

    Args:
        command: Command list to execute.
        path: Workspace path (used as CWD).
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.

    Returns:
        CompletedProcess with returncode, stdout, stderr.
    """
    result = subprocess.run(command, cwd=path, check=True, capture_output=True, text=True, timeout=timeout)
    command_log.append(
        {
            "command": " ".join(command),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )
    return result


def ddev_health_check(
    path: Path,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> None:
    """Verify DDEV instance health.

    Args:
        path: Workspace path (used as CWD for ddev commands).
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.

    Raises:
        DDEVError: If the health check fails.
    """
    try:
        run_logged_command(["ddev", "describe"], path, command_log, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        from nichebench.execution.runtime.workspace.model import DDEVError

        raise DDEVError(f"DDEV health check failed: {exc.stderr}") from exc


def patch_settings_php(path: Path, command_log: list[dict[str, object]]) -> None:
    """Append config_sync_directory to settings.php after site:install.

    Drupal's default falls back to sites/default/files/sync when
    $settings['config_sync_directory'] is unset, but the runtime pack
    keeps config in config/sync/ (git-tracked).  Patching here ensures
    `ddev drush cex --yes` writes there and `drush config:status` compares
    against the correct directory.

    settings.php is gitignored so this patch is invisible to final.diff.

    Args:
        path: Workspace path.
        command_log: Shared command log list to append entries to.
    """
    settings_php = path / "web" / "sites" / "default" / "settings.php"
    if not settings_php.exists():
        return
    content = settings_php.read_text()
    marker = "# [nichebench] config_sync_directory"
    if marker in content:
        return  # already patched
    patch = f"\n{marker}\n" "$settings['config_sync_directory'] = '../config/sync';\n"
    settings_php.write_text(content + patch)
    command_log.append(
        {
            "command": "patch settings.php: config_sync_directory → config/sync",
            "returncode": 0,
            "stdout": "Patched settings.php",
            "stderr": "",
        }
    )


def ddev_start(
    path: Path,
    setup_mode: str,
    command_log: list[dict[str, object]],
    setup_warnings: list[str],
    timeout: Optional[int] = None,
    post_setup_commands: Optional[list] = None,
) -> None:
    """Start DDEV and apply configured setup mode.

    Args:
        path: Workspace path (used as CWD for ddev commands).
        setup_mode: Either "config_import" or "db_snapshot".
        command_log: Shared command log list to append entries to.
        setup_warnings: Shared warnings list for non-fatal setup issues.
        timeout: Optional command timeout in seconds.
        post_setup_commands: Optional list of commands to run after setup.

    Raises:
        DDEVError: If DDEV commands fail.
    """
    from nichebench.execution.runtime.workspace.cleanup import (
        best_effort_network_hygiene,
    )
    from nichebench.execution.runtime.workspace.model import DDEVError

    # Best-effort network hygiene before starting a new DDEV project.
    best_effort_network_hygiene(path, "", command_log, timeout=timeout)

    try:
        run_logged_command(["ddev", "start"], path, command_log, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        if is_network_pool_exhaustion_error(exc):
            best_effort_network_hygiene(path, "", command_log, timeout=timeout)
            run_logged_command(["ddev", "start"], path, command_log, timeout=timeout)
        else:
            raise DDEVError(f"DDEV command failed: {exc.stderr}") from exc

    run_logged_command(["ddev", "composer", "install"], path, command_log, timeout=timeout)

    if setup_mode == "config_import":
        run_logged_command(["ddev", "drush", "site:install", "minimal", "--yes"], path, command_log, timeout=timeout)
        patch_settings_php(path, command_log)

        # Sync site UUID from config/sync/system.site.yml so cim doesn't reject it
        site_yml = path / "config" / "sync" / "system.site.yml"
        if site_yml.exists():
            import yaml  # local import — pyyaml is a transitive dep

            site_data = yaml.safe_load(site_yml.read_text())
            site_uuid = site_data.get("uuid", "")
            if site_uuid:
                run_logged_command(
                    ["ddev", "drush", "config-set", "system.site", "uuid", site_uuid, "--yes"],
                    path,
                    command_log,
                    timeout=timeout,
                )

        run_logged_command(
            ["ddev", "drush", "cim", "--yes", "--source=/var/www/html/config/sync"],
            path,
            command_log,
            timeout=timeout,
        )

        # Seed fixtures — site-specific command, ignore failure gracefully
        try:
            result = subprocess.run(
                ["ddev", "drush", "nichejobs:seed"],
                cwd=path,
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
                setup_warnings.append(warning)
            command_log.append(log_entry)
        except subprocess.TimeoutExpired as exc:
            warning = f"nichejobs:seed timed out after {timeout}s"
            setup_warnings.append(warning)
            command_log.append({"command": "ddev drush nichejobs:seed", "warning": warning, "error": str(exc)})
        except Exception as exc:
            warning = f"nichejobs:seed raised unexpected exception: {exc}"
            setup_warnings.append(warning)
            command_log.append({"command": "ddev drush nichejobs:seed", "warning": warning, "error": str(exc)})

    elif setup_mode == "db_snapshot":
        snapshot_gz = path / ".ddev" / "db_snapshots" / f"{path.name}.sql.gz"
        snapshot_sql = path / "db.sql"
        if snapshot_gz.exists():
            run_logged_command(["ddev", "import-db", f"--file={snapshot_gz}"], path, command_log, timeout=timeout)
        elif snapshot_sql.exists():
            run_logged_command(["ddev", "import-db", f"--file={snapshot_sql}"], path, command_log, timeout=timeout)

    if post_setup_commands:
        for cmd in post_setup_commands:
            run_logged_command(cmd, path, command_log, timeout=timeout)

    ddev_health_check(path, command_log, timeout=timeout)


def ddev_stop(
    path: Path,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> None:
    """Stop, remove data, and unlist DDEV instance.

    Args:
        path: Workspace path (used as CWD for ddev commands).
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.

    Raises:
        DDEVError: If the stop command fails.
    """
    from nichebench.execution.runtime.workspace.model import DDEVError

    try:
        run_logged_command(["ddev", "delete", "--omit-snapshot", "-y"], path, command_log, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise DDEVError(f"DDEV stop failed: {exc.stderr}") from exc


def run_ddev_drush(
    args: list[str],
    path: Path,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
    """Run a drush command through DDEV and record it.

    Args:
        args: Drush command arguments.
        path: Workspace path (used as CWD for ddev commands).
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.

    Returns:
        CompletedProcess with returncode, stdout, stderr.
    """
    return run_logged_command(["ddev", "drush", *args], path, command_log, timeout=timeout)
