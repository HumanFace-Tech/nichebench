# Runtime Smoke Tests

> **Related:** [Architecture & Lifecycle](./runtime-architecture-lifecycle.md) | [Task Authoring](./runtime-task-authoring.md) | [Diagnostics Playbook](./runtime-diagnostics-playbook.md)

---

Smoke tests validate the runtime harness end-to-end using a single task branch before running full batches.

## Smoke Procedure

The canonical smoke procedure uses `scripts/runtime_smoke.py` against an isolated workspace:

```bash
# 1. Create a clean workspace from a known task branch
git worktree add /tmp/nb-smoke-workspace \
  -b temp/smoke-$(date +%Y%m%d%H%M%S) \
  origin/task/drupal_runtime_001

# 2. Start DDEV in the workspace
cd /tmp/nb-smoke-workspace/web
ddev start
ddev drush cim -y

# 3. Run the smoke script
python /workspaces/nichebench/scripts/runtime_smoke.py \
  --workspace /tmp/nb-smoke-workspace \
  --json

# 4. Inspect output
# Expected: all checks pass, no critical failures, Rich table shows:
#   Checks: 21/21, Deterministic: 100%, Judge: varies, Hybrid: >0.7

# 5. Cleanup
ddev stop --remove-data -y
git worktree remove /tmp/nb-smoke-workspace
```

## What the Smoke Script Validates

`runtime_smoke.py` exercises the full runtime executor path against the workspace:

1. **DDEV bootstrap** — site starts cleanly, config import succeeds
2. **Task injection** — `TASK.md` is present and readable
3. **Check execution** — deterministic checks run against the baseline (pre-MUT) state
4. **Artifact emission** — `metadata.json`, `runtime_trace.json`, `checks.json` are written
5. **Rich output** — summary table printed to stdout

The script does **not** run the full MUT agent or judge — it validates the harness plumbing only.

## Smoke Exit Codes

| Code | Meaning |
|---|---|
| `0` | Smoke passed — harness plumbing is healthy |
| `1` | Smoke failed — investigate `runtime_trace.json` and `run.log` |
| `2` | Setup failed — DDEV or workspace issue, not a harness bug |

## JSON Output Schema

When `--json` is passed, `runtime_smoke.py` outputs:

```json
{
  "status": "pass|fail|error",
  "workspace": "/path/to/workspace",
  "ddev_status": "running|stopped|error",
  "checks_total": 21,
  "checks_passed": 21,
  "checks_failed": 0,
  "deterministic_pct": 1.0,
  "artifacts_present": ["metadata.json", "runtime_trace.json", "checks.json"],
  "error": null
}
```

## When to Run Smoke Tests

| Event | Required? |
|---|---|
| After `git pull` with runtime changes | Yes |
| After config schema changes | Yes |
| Before a full benchmark batch | Yes |
| After DDEV version upgrade | Yes |
| On a fresh clone | Yes |

## Common Smoke Failures

### DDEV won't start

```bash
ddev describe  # check for port conflict
ddev poweroff && ddev start
```

### Config import fails

```bash
ddev drush cim -y   # retry manually
ddev drush config-sync  # check for schema errors
```

### Checks fail on baseline

Some checks are `fail_to_pass` — they are expected to fail before the MUT runs. Verify the check type before assuming a harness bug.

### Artifact files missing

Check `runtime_artifact_retention` in your config. Use `standard` or `full` to capture all artifacts.
