# Runtime Tasks — Architecture & Lifecycle

> **Canonical reference:** [Runtime Task Authoring](./runtime-task-authoring.md) | [Smoke Tests](./runtime-smoke-tests.md) | [Diagnostics Playbook](./runtime-diagnostics-playbook.md) | [Reporting & Scoring](./runtime-reporting-scoring.md)

---

## What Makes Runtime Tasks Different

Every runtime task:

1. Checks out a **task branch** (`task/drupal_runtime_NNN`) from the runtime pack into an isolated workspace
2. Starts a **DDEV Drupal environment** (`ddev start` + `ddev drush cim -y`)
3. Injects `TASK.md` (from the task branch root) as authoritative agent instructions
4. Runs the **agent-under-test** (OpenCode in cage mode)
5. Captures an **artifact bundle**
6. Runs **deterministic checks** (file existence, grep, drush commands, static analysis)
7. Optionally runs an **LLM judge** against the artifacts
8. Computes a **hybrid score**

Runtime tasks are **disabled by default**. Enable with:

```yaml
evaluation:
  enable_runtime_tasks: true
```

---

## Lifecycle Stages

```
config_resolution
        │
        ▼
workspace_setup ────────────────────────────────────────┐
        │                                                │
        ▼                                                ▼
environment_bootstrap                           TASK.md injection
  (DDEV start + import)                                 │
        │                                               ▼
        ▼                                        agent_execution
                                          (OpenCode cage MUT)
        │                                               │
        ▼                                               ▼
deterministic_checks                        final workspace state
        │                                               │
        │◄───────── run.log / trajectory ───────────────┘
        ▼
  judge_scoring (optional)
        │
        ▼
    cleanup
        │
        ▼
   final score
```

Stage evidence is recorded in `runtime_trace.json` with `status: passed | failed | skipped`.

---

## Cage Mode (Default)

Runtime tasks default to `cage` mode. The cage is a Docker container that:

- Uses a DDEV-capable runtime image (default: `nichebench/opencode-ddev:1.14.25`)
- Auto-builds a derived image with ddev/docker CLI if `runtime_container_ddev_auto_build: true`
- Mounts explicit audit islands (input, output-trace, ops)
- Mounts the Docker socket (`/var/run/docker.sock`) for in-agent `ddev` commands
- Applies hardening: `--cap-drop=ALL`, `no-new-privileges`, non-root user
- Optionally uses tmpfs mounts when `runtime_container_read_only: true`

### Island Mounts

| Island | Container path | Purpose |
|---|---|---|
| input | `/nichebench/islands/input` | Read-only task inputs |
| output-trace | `/nichebench/islands/output-trace` | Artifacts + trace output |
| trace subpath | `/nichebench/islands/output-trace/trace` | Trajectory files |
| ops (optional) | `/nichebench/islands/ops` | Operational helper scripts |

`metadata.json` records `island_topology` for full auditability of which paths were mounted where.

---

## Environment Isolation

| Concern | Policy |
|---|---|
| Workspace path | `workspaces/run-<task_id>-<uuid8>/` |
| DDEV project name | `nb-<safe_task_id>-<uuid8>` — parallel-safe, never collides |
| DDEV cleanup | `ddev stop --remove-data -y` + workspace dir deletion |
| Workspace retention | Set `runtime_keep_workspaces: true` to debug |

**Never share a DDEV project between runs. Never reuse a workspace dir.**

Prerequisites on host: `docker`, `ddev`.

---

## Artifact Bundle

| Retention | Artifacts |
|---|---|
| `minimal` | `metadata.json`, `runtime_trace.json` |
| `standard` | `minimal` + `checks.json`, `final.diff`, `run.log`, `trajectory.json`, `last_phpcs.txt`, `last_phpstan.txt`, `watchdog_errors.txt` when available |
| `full` | `standard` + raw/debug/browser payloads |

Static-analysis and watchdog artifacts are intended for judge/operator inspection. The LLM judge consumes the artifact bundle; it does not execute DDEV commands directly.

---

## Deterministic Checks

Three check types:

| Type | Description |
|---|---|
| `fail_to_pass` | Command expected to fail initially, pass after MUT changes |
| `pass_to_pass` | Command that must continue to pass (e.g., existing tests) |
| `path_policy` | Restricts modifications to specific directories |

Any critical check failure → `passed=False` regardless of score.

---

## Submodule

Runtime task manifests, checks, and scripts live in:

```
src/nichebench/frameworks/drupal_runtime/data/
  → HumanFace-Tech/nichebench-drupal-runtime-pack (private submodule)
```

Task branches follow `task/drupal_runtime_NNN`. `TASK.md` at the branch root is the canonical task spec — it is injected automatically. Do not put harness internals in it.
