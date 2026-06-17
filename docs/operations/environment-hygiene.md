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

# 2. Remove the matching DDEV project (deletes volumes + images).
ddev delete -y nb-drupal-runtime-001-<uuid8>

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

```bash
# Conservative prune (removes stopped containers, unused images)
docker prune

# Aggressive prune (removes all unused images, not just dangling)
docker prune -a

# DDEV poweroff (stops all projects)
ddev poweroff

# Remove dangling DDEV volumes
ddev delete -y <project> --remove-data
```

## Never Do

- **Never** share a DDEV project between runs.
- **Never** reuse a workspace directory.
- **Never** `git add .` or `git push` without explicit instruction.
- **Never** hardcode entity IDs (nids, uids) in routing, access logic, or defaults.
