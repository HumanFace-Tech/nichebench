# Runtime Task Authoring Workflow

> **Related:** [Architecture & Lifecycle](./runtime-architecture-lifecycle.md) | [Smoke Tests](./runtime-smoke-tests.md) | [Diagnostics Playbook](./runtime-diagnostics-playbook.md)

---

Runtime tasks evaluate agent performance in realistic Drupal environments using DDEV.

## Manifest Schema

Runtime tasks are defined in YAML files with `task_type: runtime`.

### Required Fields

- `id`: Unique identifier for the task.
- `source`:
  - `base_branch`: The branch in the data repository to use as a baseline.
- `environment`:
  - `setup_mode`: Either `config_import` (runs `drush cim`) or `db_snapshot` (imports `db.sql`).
- `agent`:
  - `profile`: One of `offline_cli`, `web_cli`, `web_plus_browser`.
- `checks`: A list of deterministic checks.
- `scoring`:
  - `deterministic_weight`: Weight for deterministic checks (default 0.7).
  - `judge_weight`: Weight for LLM judge rubric (default 0.3).
- `deliverables`: List of expected files or changes.

## Runtime Execution Settings

`evaluation` config supports:

- `runtime_mode`: `cage` (default; legacy `container` alias still accepted) or `host` (compatibility override)
- `runtime_container_image`: image used for container mode
- `runtime_container_user`: container user (default `1000:1000`)
- `runtime_container_read_only`: enable `--read-only` for the job container (default `false`)
- `runtime_timeout_minutes`: runtime task timeout
- `runtime_max_workers`: max concurrent runtime workers
- `runtime_artifact_retention`: `minimal`, `standard` (default), or `full`
- `runtime_keep_workspaces`: retain runtime workspaces after each run (legacy `keep_workspaces` still works)
- `runtime_enable_diagnostics`: emit `runtime_trace.json` and failure taxonomy metadata (default `true`)

Container mode adds hardening defaults (`--cap-drop=ALL`, `no-new-privileges`, non-root user, and optional tmpfs mounts when `runtime_container_read_only` is enabled). This reduces risk, but the Docker socket bind remains highly privileged and should be treated as such.

Cage runs expose explicit mount islands for auditability:

- input island: `/nichebench/islands/input`
- output/trace island: `/nichebench/islands/output-trace`
- trace subpath: `/nichebench/islands/output-trace/trace`
- optional ops island: `/nichebench/islands/ops`

The exact island mapping used for each run is persisted in runtime `metadata.json` under `island_topology`.

## Runtime Execution Flow

```text
manifest/checks YAML
         │
         ▼
isolated workspace checkout ──► DDEV start/import ──► TASK.md (+ optional HINTS.md)
         │                                                  │
         │                                                  ▼
         │                                           OpenCode cage MUT
         │                                                  │
         ▼                                                  ▼
deterministic checks ◄──── final workspace state ◄──── run.log / trajectory
         │
         ▼
artifact bundle + optional LLM judge + hybrid score
```

## Artifact Bundle

Runtime runs emit a normalized bundle under the results run directory. Retention controls how much is persisted:

| Retention | Artifacts |
|---|---|
| `minimal` | `metadata.json`, `runtime_trace.json` |
| `standard` | `minimal` + `checks.json`, `final.diff`, `run.log`, `trajectory.json`, `last_phpcs.txt`, `last_phpstan.txt`, `watchdog_errors.txt` when available |
| `full` | `standard` + optional raw/debug/browser payloads when enabled |

Optional browser artifacts can be attached when enabled by profile/config.

Static-analysis and watchdog artifacts are intended for judge/operator inspection.
The LLM judge consumes the artifact bundle; it does not execute DDEV commands directly.

### Deterministic Checks

- `fail_to_pass`: A command that is expected to fail initially and pass after the agent's changes.
- `pass_to_pass`: A command that must continue to pass (e.g., existing tests).
- `path_policy`: Restricts modifications to specific directories.

## Branch Maintenance

Tasks should be authored on branches in the `nichebench-drupal-runtime-pack` repository following the `task/*` prefix.

1. Create a branch from `seed/main`.
2. Implement the "broken" state or the starting point for the task.
3. Commit and push the branch.
4. Reference the branch in the task manifest `source.base_branch`.

The sync script `scripts/sync_data_branches.py` can be used to propagate updates from `seed/main` to all `task/*` branches.

## Cleanup and Maintenance

Use `scripts/runtime_maintenance.py` for conservative disk cleanup:

```bash
python scripts/runtime_maintenance.py cleanup-workspaces --dry-run
python scripts/runtime_maintenance.py prune-docker --dry-run
python scripts/runtime_maintenance.py prune-docker --aggressive
```

Defaults are conservative; aggressive pruning is opt-in.
