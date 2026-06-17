# NicheBench — Agent Notes

Operational guidance for contributors. For system architecture, see [docs/architecture/index.md](../docs/architecture/index.md). For runtime deep-dives, see [docs/runtime/index.md](../docs/runtime/index.md).

---

## Ground Rules

- **Read before writing.** Always read the relevant source file before editing it.
- **No global state leaks.** `litellm.api_base` is **not** mutated by `LiteLLMClient.generate_with_messages`; the per-call `api_base` is passed directly to `litellm.completion`. Do not introduce code that mutates this global. The LangGraph `mut.py` runner no longer needs a `finally` reset.
- **No hardcoded IDs.** No nids, uids, or entity IDs in routing, access logic, or defaults.
- **No commits unless asked.** Never `git add .` or `git push` without explicit instruction.
- **Host cleanliness.** Runtime tasks create isolated workspaces and DDEV projects. Always let teardown run — do not leave DDEV projects or `workspaces/` dirs behind.
- **Test after changes.** `poetry run pytest -q tests/unit` must stay green. Run `poetry run ruff check src tests` and `poetry run mypy src` too.

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

## Repo Map

```
src/nichebench/
  cli/              CLI commands + Rich UI
  config/           nichebench_config.py — model/eval config, profiles
  core/             datamodel, discovery, profiles, loader_yaml
  execution/        orchestrator, runners, result, persistence
    runtime/        runtime executor, workspace, DDEV, checks, trajectory, artifacts
      executor/     runtime execution stages and flow
      trajectory/   session files, sqlite, polling, debug_dump
      artifacts/    persistence, validation, tool_policy
  providers/        LiteLLM client, judge adapter, langgraph code agent
  metrics/          DeepEval-compatible metric classes
  frameworks/
    drupal/         Classic tasks (quiz, code_generation, bug_fixing)
      data/         → submodule: nichebench-data-drupal (private)
    drupal_runtime/ Runtime tasks
      data/         → submodule: nichebench-drupal-runtime-pack (private)
  utils/            git.py, io.py

docs/               Full documentation shelf
scripts/            runtime_smoke.py, runtime_maintenance.py, sync_data_branches.py
tests/unit/         unit tests
openspec/           design proposals and task specs
nichebench.sample.yml  ← copy to nichebench.yml to configure a run
```

> **Note:** `core/executor.py` and `core/workspace.py` (legacy shim paths) were removed. Runtime code lives under `execution/runtime/`.

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

| Score | Source | Formula |
|---|---|---|
| **Deterministic** | Checks defined in manifest | `passed_checks / total_checks` |
| **Judge** | LLM judge evaluating artifact bundle | Per rubric in manifest |
| **Hybrid** | Weighted blend | `deterministic_weight × deterministic + judge_weight × judge` |

- Any critical check failure → `passed=False` regardless of score
- Default threshold: `0.7`

See [docs/runtime/runtime-reporting-scoring.md](../docs/runtime/runtime-reporting-scoring.md) for full detail.

---

## Environment Cleanup

Runtime tasks can leave zombie DDEV projects and Docker containers if aborted. See [docs/operations/environment-hygiene.md](../docs/operations/environment-hygiene.md) for the full cleanup procedure.

Quick check:

```bash
pgrep -af "opencode run|nichebench run" || true
docker ps --format '{{.ID}} {{.Names}}'
ddev list
```

---

## Dependency Notes

`litellm` is pinned to `>=1.75.8,<1.82.6`. Do not widen this range without testing. The upper bound guards against breaking API changes that affect both MUT and judge call paths.

---

## Documentation Links

| Topic | Doc |
|---|---|
| Architecture & execution layers | [docs/architecture/nichebench-harness-architecture.md](../docs/architecture/nichebench-harness-architecture.md) |
| Runtime architecture & lifecycle | [docs/runtime/runtime-architecture-lifecycle.md](../docs/runtime/runtime-architecture-lifecycle.md) |
| Runtime task authoring | [docs/runtime/runtime-task-authoring.md](../docs/runtime/runtime-task-authoring.md) |
| Runtime smoke tests | [docs/runtime/runtime-smoke-tests.md](../docs/runtime/runtime-smoke-tests.md) |
| Runtime diagnostics playbook | [docs/runtime/runtime-diagnostics-playbook.md](../docs/runtime/runtime-diagnostics-playbook.md) |
| Runtime reporting & scoring | [docs/runtime/runtime-reporting-scoring.md](../docs/runtime/runtime-reporting-scoring.md) |
| Classic task docs | [docs/tasks/index.md](../docs/tasks/index.md) |
| Operations & hygiene | [docs/operations/index.md](../docs/operations/index.md) |
