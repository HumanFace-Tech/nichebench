# Environment Hygiene & Cleanup

> **Related:** [Ground Rules](../../AGENTS.md#ground-rules) | [Runtime Diagnostics Playbook](../runtime/runtime-diagnostics-playbook.md)

---

## Host Prerequisites

| Tool | Purpose |
|---|---|
| `docker` | Runtime cage execution |
| `ddev` | Drupal local environment |

## Zombie / Stale-Run Cleanup (OpenCode Cage)

If a `nichebench run` is aborted (ESC, CLI close, shell drop), the cage container and the DDEV project can **survive the wrapper**. Symptoms:

- Stray Docker container holding `opencode run ...`
- Lingering `ddev-nb-drupal-runtime-001-*` containers
- `ddev list` showing a non-`stopped` project that no longer matches the live workspace

### Inspect

```bash
pgrep -af "opencode run|nichebench run" || true
docker ps --format '{{.ID}} {{.Names}}'
ddev list
```

### Clean Up (Safe, Non-Interactive)

```bash
# 1. Kill the stray MUT container
#    DO NOT kill PID of `opencode . --continue` — that's the active interactive session.
docker rm -f <cage_container_id_or_name>

# 2. Stop the matching DDEV project and drop its data volume.
#    Manual smoke cleanup intentionally uses `ddev stop` (not `ddev delete`,
#    which is the executor's internal cleanup command).
ddev stop --remove-data --omit-snapshot nb-drupal-runtime-001-<uuid8>

# 3. Poweroff DDEV if you want a fully clean slate.
ddev poweroff
```

### Verify

- `pgrep` empty (except the live interactive `opencode . --continue`)
- `docker ps` shows only `ddev-router` and `ddev-ssh-agent`
- `ddev list` shows no active DDEV projects

Only then start a fresh canonical run.

## Runtime Pack AGENTS.mut.md Hot-Fix to Port

When updating the Drupal runtime pack's MUT-facing `AGENTS.mut.md`, follow this validation loop:

1. Run `ddev composer cs-fix` before hand-editing PHPCS formatting issues.
2. After `cs-fix`, manually fix only the remaining non-fixable PHPCS issues. Do not broadly hand-indent docblocks or churn formatting.
3. Run `ddev composer cs web/modules/custom/nichejobs_application` after manual formatting fixes.
4. Run module-scoped PHPStan after each meaningful fix batch:

   ```bash
   ddev exec -- vendor/bin/phpstan analyse --configuration=phpstan.neon web/modules/custom/nichejobs_application
   ```

5. **Do not finish or summarize as complete** while PHPCS, PHPStan, syntax checks, config status, or watchdog PHP checks are known to be failing.

## Workspace Retention

By default, runtime workspaces are deleted after each run. To retain for debugging:

```yaml
evaluation:
  runtime_keep_workspaces: true
```

Or use the maintenance script to clean up stale workspaces:

```bash
python scripts/runtime_maintenance.py cleanup-workspaces --dry-run
python scripts/runtime_maintenance.py cleanup-workspaces   # actual cleanup
```

## DDEV Disk Cleanup

`docker prune` and `docker prune -a` are **not valid commands**. Use the
real prune subcommands below, or the bundled helper.

### Manual Prune

```bash
# Conservative prune — clears dangling builder cache, then stops/removes
# stopped containers, dangling networks, and dangling images.
docker builder prune -f
docker system prune -f

# Aggressive prune — also removes ALL unused images (not just dangling).
docker system prune -af

# DESTRUCTIVE: also removes every unused Docker volume on the host.
# Volumes not attached to a running container are deleted permanently;
# any database/file data stored there is gone. Only run this if you
# are sure no other project depends on those volumes.
docker system prune -af --volumes

# DDEV poweroff (stops all projects; safe)
ddev poweroff

# Stop a specific DDEV project and drop its data volume.
# NOTE: This is the manual smoke cleanup command. The executor's internal
# cleanup is `ddev delete --omit-snapshot -y` (see ddev.py); operators
# running smoke tests after an aborted run should use `ddev stop` instead.
ddev stop --remove-data --omit-snapshot <project>
```

### Bundled Helper

`scripts/runtime_maintenance.py` exposes a `prune-docker` subcommand that
wraps the same calls and adds a dry-run mode:

```bash
# Show actions without running them
python scripts/runtime_maintenance.py prune-docker --dry-run

# Conservative prune (builder cache + system prune, no volumes)
python scripts/runtime_maintenance.py prune-docker

# Aggressive prune — also adds `docker system prune -af --volumes`.
# Same destructive volume/image impact as the manual command above.
python scripts/runtime_maintenance.py prune-docker --aggressive
```

`--aggressive` and `--volumes` both reach for unused Docker volumes. Run
them only when you understand the destructive impact.

## Never Do

- **Never** share a DDEV project between runs.
- **Never** reuse a workspace directory.
- **Never** `git add .` or `git push` without explicit instruction.
- **Never** hardcode entity IDs (nids, uids) in routing, access logic, or defaults.
