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
| `quiz` | ✅ Active | LLM-judged multiple choice; parser result is recorded for audit |
| `code_generation` | ✅ Active | Single-turn code generation, direct LLM judge |
| `bug_fixing` | ✅ Active | Single-shot fix plus finalization nudge |
| `runtime` (`drupal_runtime`) | ✅ Active | Full agentic runtime on live DDEV. Canonical agentic type. |

The deprecated `code_agent` prototype has been removed. Use `drupal_runtime` for all new agentic tasks. Existing result files and historical records remain unchanged.

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
  providers/        LiteLLM client and judge adapter
  metrics/          quiz answer parser (audit-only); direct LiteLLM judge for quiz/code/bug (no DeepEval)
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

- **Quiz:** `LiteLLMJudge.score_quiz` selects a letter from the candidate answer; NicheBench compares that letter with the gold letter for a binary score. The deterministic parser records `selected`, `parse_reason`, and `agrees` for audit only. Explicit abstention (`selected: null`, `pass: false`, non-empty explanation) is a scored zero and remains in averages. `--judge` selects the quiz judge model.
- **Quiz, code generation, and bug fixing:** judge calls retry up to 3 times. Exhausted quiz failures (`judge_unavailable`, `judge_invalid_json`, `judge_missing_selected`, or `judge_out_of_range`) count as `judge_errors`; invalid quiz task data uses `error_kind=config_error` and counts as `config_errors`. Both error classes are excluded from averages. Code/bug use direct `LiteLLMJudge` rubric evaluation (no DeepEval); NicheBench recomputes and clamps the score from criteria (true=1, partial=0.5, false/missing=0), and excludes judge-error rows from averages.
- **Runtime:** deterministic checks and judge score form a weighted hybrid recomputed from manifest criteria, each at most once. Judge outages/malformed output and runtime setup/image/Docker/network/CLI/config failures are counted as `judge_errors` or `config_errors` and excluded from averages, with no deterministic-only fallback presented as a comparable hybrid score. Reports show `ERROR`/`N/A` rather than 0% for excluded rows. Any critical check failure sets `passed=False`; the default threshold is `0.7`.

Quiz data choices are plain text without letter prefixes. The quiz data repair and rebalance was applied in the private data repo. `dataset_version: quiz-v2`; distribution is A=25 and B–F=24 each. Do not add prefixes, truncate choices, or introduce mappings.

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

## Quality Gates

### Unit Gates (no Docker/DDEV required)

Run these after any code change:

```bash
poetry run pytest -q tests/unit
poetry run ruff check src tests
poetry run mypy src
```

All three must pass before committing.

### Runtime Smoke (requires Docker + DDEV)

Runtime tasks require a Docker-capable host with DDEV installed. These checks also validate that teardown runs cleanly:

```bash
# Quick health check
docker --version
ddev version

# Runtime smoke — requires enable_runtime_tasks: true in nichebench.yml
poetry run nichebench run drupal_runtime runtime --ids drupal_runtime_001 \
  --model groq/llama-3.3-70b-versatile
```

> **Sequential DDEV constraint:** Only one DDEV project can be active on a host at a time. Do not attempt parallel runtime runs — they will produce resource conflicts.

CI does **not** run runtime smoke. It only runs the unit gates above.

---

## Dependency Notes

The project targets Python **3.13–3.14** (`>=3.13,<3.15`). `litellm` is pinned
to `==1.106.7`; do not change it without testing the MUT and judge call paths.
`pandas` and `pyarrow` were removed as unused. `ruff` uses an explicit `select`
list (not `extend-select`) so newer Ruff releases do not silently expand the
rule set.

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
