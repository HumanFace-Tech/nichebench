# Runtime Smoke Tests

> **Related:** [Architecture & Lifecycle](./runtime-architecture-lifecycle.md) | [Task Authoring](./runtime-task-authoring.md) | [Diagnostics Playbook](./runtime-diagnostics-playbook.md)

---

Smoke tests validate the Drupal runtime environment (DDEV, Drush, database, config) using read-only probes. Use this against an isolated/harness-created DDEV workspace or a known task baseline before running full batches.

## Smoke Procedure

Smoke tests are **environment preflight only** — they validate the DDEV/Drush/Drupal stack is healthy. They do **not** run the runtime executor, inject tasks, or evaluate judge scoring.

The procedure runs five read-only probes against an **existing, already-bootstrapped** DDEV workspace — typically one created by the runtime harness during a prior task execution, or a task baseline that has already been brought up by its owning task pack.

> **Note — bootstrap is owned by the task pack, not the public harness.** This procedure does not bootstrap a fresh baseline. The runtime pack's `scripts/bootstrap_seed.sh` and any Gin/bootstrap fixes live in the private task-pack repository. See your task pack's own bootstrap documentation for bringing up a fresh workspace. The public harness does **not** claim that `bash scripts/bootstrap_seed.sh` succeeds on a fresh private pack checkout.

```bash
# 1. Run the smoke script against an existing, already-bootstrapped DDEV workspace
python /workspaces/nichebench/scripts/runtime_smoke.py \
  --workspace /path/to/workspace \
  --json

# 2. Inspect output
# Expected: all 5 checks pass

# 3. Cleanup
ddev stop --remove-data --omit-snapshot
```

> **Source-tree safety:** Always run smoke against an **isolated workspace**, never the nichebench source tree. DDEV projects must not be started inside the harness source directory.

## What the Smoke Script Validates

`runtime_smoke.py` probes the DDEV environment with **five read-only checks**:

| # | Check | Command | Timeout |
|---|---|---|---|
| 1 | `ddev_status` | `ddev status` | 60s |
| 2 | `composer_validate` | `ddev composer validate --no-interaction` | 120s |
| 3 | `drush_status` | `ddev drush status --fields=bootstrap,drupal-version` | 60s |
| 4 | `drush_sql_query` | `ddev drush sql:query SELECT 1` | 30s |
| 5 | `drush_config_status` | `ddev drush config:status` | 60s |

All checks are **read-only probes** — no repair, no task injection, no artifact emission.

> **Note — `drush_config_status` is stricter than exit-code-only.**
> Drush's `config:status` command can exit 0 even when drift exists between the
> active config and `config/sync/`. The smoke check therefore requires **both**:
> (a) the command exits 0, **and** (b) stdout contains the `No differences`
> sentinel that Drush emits when the active config matches the sync directory.
> If drift is detected, the check fails with a `failure_reason` that points at
> `ddev drush config:import` / `config:export` as remediation.

## Smoke Exit Codes

| Code | Meaning |
|---|---|
| `0` | All checks passed — environment is healthy |
| `1` | One or more checks failed — investigate Drush output |
| `2` | Setup failed — workspace not found or not a DDEV project |

## JSON Output Schema

When `--json` is passed, `runtime_smoke.py` outputs:

```json
{
  "workspace": "/path/to/workspace",
  "total": 5,
  "passed": 5,
  "failed": 0,
  "checks": [
    {
      "name": "ddev_status",
      "command": "ddev status",
      "passed": true,
      "returncode": 0,
      "stdout": "...",
      "stderr": "",
      "failure_reason": ""
    },
    ...
  ]
}
```

`failure_reason` is an empty string for passing checks. For failing checks it
explains why — non-zero exit, missing success marker, command timeout, or
launch failure.

## Sequential DDEV Constraint

Only one DDEV project can be active on a host at a time. Do not run parallel smoke tests — they will produce resource conflicts.

## When to Run Smoke Tests

| Event | Required? |
|---|---|
| After DDEV version upgrade | Yes |
| After Drupal core/module upgrade | Yes |
| Before a full benchmark batch | Yes |
| Against a newly bootstrapped workspace | Yes |

## Common Failures

### DDEV won't start

```bash
ddev describe  # check for port conflict
ddev poweroff && ddev start
```

### `composer_validate` fails

```bash
ddev composer validate   # retry manually
```

### Drush bootstrap fails

```bash
ddev drush status         # check bootstrap and Drupal version
ddev drush cim -y         # retry config import
```

### SQL query fails

```bash
ddev drush sql:connect     # verify database is reachable
```

### Config status non-zero

```bash
ddev drush config:status   # list sync issues manually
```
