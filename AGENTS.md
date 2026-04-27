# NicheBench — Agent Notes

NicheBench is a CLI benchmarking harness for evaluating LLMs on **framework-specific tasks**.
The primary focus is Drupal. The canonical agentic task type is `drupal_runtime`.

---

## Ground Rules

- **Read before writing.** Always read the relevant source file before editing it.
- **No global state leaks.** `litellm.api_base` is reset after every LangGraph agent run — do not break this.
- **No hardcoded IDs.** No nids, uids, or entity IDs in routing, access logic, or defaults.
- **No commits unless asked.** Never `git add .` or `git push` without explicit instruction.
- **Host cleanliness.** Runtime tasks create isolated workspaces and DDEV projects. Always let teardown run — do not leave DDEV projects or `workspaces/` dirs behind.
- **Test after changes.** `poetry run pytest -q tests/unit` must stay green. Run `poetry run ruff check src tests` and `poetry run mypy src` too.

---

## Repo Map

```
src/nichebench/
  cli/              CLI commands + Rich UI
  config/           nichebench_config.py — model/eval config, profiles
  core/             executor.py, workspace.py, scoring.py, datamodel.py,
                    loader_yaml.py, discovery.py, validation.py, profiles.py
  providers/        LiteLLM client, judge adapter, conversation manager,
                    LangGraph code agent
  metrics/          DeepEval-compatible metric classes
  frameworks/
    drupal/         Classic tasks (quiz, code_generation, bug_fixing)
      data/         → submodule: nichebench-data-drupal (private)
    drupal_runtime/ Runtime tasks
      data/         → submodule: nichebench-drupal-runtime-pack (private)
  utils/            git.py, io.py

docs/               runtime-task-authoring.md  ← read this before authoring runtime tasks
scripts/            runtime_maintenance.py, sync_data_branches.py
tests/unit/         unit tests
openspec/           design proposals and task specs (planning artefacts)
nichebench.sample.yml  ← copy this to nichebench.yml to configure a run
```

---

## Task Categories

| Category | Status | Notes |
|---|---|---|
| `quiz` | ✅ Active | Static Q&A, LLM-as-a-Judge |
| `code_generation` | ✅ Active | Single/multi-turn code gen, LLM-as-a-Judge |
| `bug_fixing` | ✅ Active | Multi-turn bug fix conversation |
| `code_agent` | ⛔ Deprecated | Early agentic prototype — no real env, no checks pipeline. Do not add tasks here. |
| `runtime` (`drupal_runtime`) | ✅ Active | Full agentic runtime on live DDEV. Canonical agentic type. |

`drupal_runtime` is the correct model for all new agentic tasks.

---

## Runtime Tasks — What Makes Them Different

Every runtime task:
1. Checks out a **task branch** (`task/drupal_runtime_NNN`) from the runtime pack into an isolated workspace
2. Starts a **DDEV Drupal environment** (`ddev start` + `ddev drush cim -y`)
3. Injects `TASK.md` (from the task branch root) as authoritative agent instructions
4. Runs the **agent-under-test** (OpenCode in host or container mode)
   - Cage runs mount explicit islands: input island (`/nichebench/islands/input`),
     output/trace island (`/nichebench/islands/output-trace`, trace subpath `/nichebench/islands/output-trace/trace`),
     and optional ops island (`/nichebench/islands/ops`)
   - Cage uses DDEV-capable runtime image by default (auto-build supported) with:
     - `runtime_container_enable_ddev: true` enables DDEV support check
     - `runtime_container_ddev_auto_build: true` auto-builds derived image with ddev/docker CLI if needed
     - `runtime_container_ddev_image` specifies derived image tag (default: `nichebench/opencode-ddev:1.14.25`)
     - Docker socket mount enables in-agent `ddev` commands for Drupal tasks
5. Captures a **artifact bundle** — `final.diff`, `run.log`, `checks.json`, `metadata.json`, `phpcs.json`, `phpstan.json`
6. Runs **deterministic checks** (file existence, grep, drush commands, static analysis)
7. Optionally runs an **LLM judge** against the artifacts
8. Computes a **hybrid score** (default: 50% deterministic / 50% judge — per-manifest)

Runtime tasks are **disabled by default**. Enable with:
```yaml
evaluation:
  enable_runtime_tasks: true
```

---

## Environment Isolation

- Workspace path: `workspaces/run-<task_id>-<uuid8>/`
- DDEV project name: `nb-<safe_task_id>-<uuid8>` — parallel-safe, never collides
- Cage island topology:
  - input island: `/nichebench/islands/input`
  - output/trace island: `/nichebench/islands/output-trace` (trace subpath: `/nichebench/islands/output-trace/trace`)
  - optional ops island: `/nichebench/islands/ops`
- Runtime `metadata.json` records `island_topology` (host/container path mapping)
- Cleanup: `ddev stop --remove-data -y` + workspace dir deletion, unless `runtime_keep_workspaces: true`
- **Never** share a DDEV project between runs. **Never** reuse a workspace dir.

Prerequisites on the host: `docker`, `ddev`.

---

## External Repos (Submodules)

| Submodule path | Repo | Purpose |
|---|---|---|
| `src/nichebench/frameworks/drupal/data` | `HumanFace-Tech/nichebench-data-drupal` | Quiz / code_gen / bug_fixing tasks |
| `src/nichebench/frameworks/drupal_runtime/data` | `HumanFace-Tech/nichebench-drupal-runtime-pack` | Runtime task manifests, checks, scripts |

Clone with `git clone --recursive` or run `git submodule update --init --recursive`.

Runtime task branches in the pack follow the convention `task/drupal_runtime_NNN`.
`TASK.md` at the root of a task branch is the **canonical task spec** — it is injected into the agent prompt automatically. Do not put harness internals in it.

---

## Configuration

Copy `nichebench.sample.yml` → `nichebench.yml`. Key sections:
- `mut` — model under test (provider/model/params)
- `judge` — judge model
- `evaluation` — parallelism, runtime flags, artifact retention
- `profiles` — named overrides (e.g. `groq`, `anthropic`, `ollama_local`)

Precedence: **CLI args > env vars > profile > defaults**

Relevant env vars (put in `.env`):
```
GROQ_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
NICHEBENCH_JUDGE=openai/gpt-4o   # optional override
```

---

## Dependency Notes

`litellm` is pinned to `>=1.75.8,<1.82.6`. Do not widen this range without testing.
The upper bound guards against breaking API changes that affect both MUT and judge call paths.

---

## Running Things

```bash
poetry install

# List everything
poetry run nichebench list

# List tasks for a framework
poetry run nichebench list-tasks drupal_runtime

# Run a specific task
poetry run nichebench run drupal_runtime runtime --ids drupal_runtime_001 \
  --model groq/llama-3.3-70b-versatile

# Tests
poetry run pytest -q tests/unit
poetry run ruff check src tests
poetry run mypy src
```

---

## Scoring Quick Reference

- **Deterministic score** = passed checks / total checks (checks defined in manifest)
- **Judge score** = LLM judge evaluating artifact bundle (checklist in manifest)
- **Hybrid score** = weighted blend, weights set per manifest (`scoring.deterministic_weight` / `scoring.llm_weight`)
- Any critical check failure → `passed=False` regardless of score
- Threshold default: `0.7`

See `src/nichebench/core/scoring.py` for the full check type list and scoring logic.
