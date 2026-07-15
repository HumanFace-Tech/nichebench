# NicheBench Documentation

NicheBench is a CLI benchmarking harness for evaluating LLMs on **framework-specific tasks**. Starting with Drupal, it features LLM-as-a-Judge evaluation, configuration-driven model management, and a Rich CLI for interactive reporting.

## Docs Shelves

### [Architecture](./architecture/index.md)
Full system design — execution layers, repo shelves, scoring internals, ASCII diagrams.

### [Runtime Tasks](./runtime/index.md)
Agentic Drupal runtime tasks on live DDEV. Covers architecture/lifecycle, task authoring, smoke tests, diagnostics playbook, and reporting/scoring semantics.

### [Classic Tasks](./tasks/index.md)
Non-runtime task categories: quiz, code generation, bug fixing.

### [Operations](./operations/index.md)
DDEV/Docker cleanup, zombie stale-run procedures, local config/run hygiene, and maintenance scripts.

## Benchmark Artifact Privacy

Public documentation describes the benchmarking methodology and scoring, not private run outcomes. Raw results, reports, local configs, credentials, and historical internal audits are not committed; historical audits remain local-only.

## Quick Reference

```bash
# Install
poetry install

# List available tasks
poetry run nichebench list

# Run a task
poetry run nichebench run drupal quiz
poetry run nichebench run drupal_runtime runtime --ids drupal_runtime_001

# Tests
poetry run pytest -q tests/unit
```

## Task Categories

| Category | Type | Notes |
|---|---|---|
| `quiz` | Classic | Static Q&A, LLM-as-a-Judge |
| `code_generation` | Classic | Single/multi-turn code gen, LLM-as-a-Judge |
| `bug_fixing` | Classic | Multi-turn bug fix conversation |
| `drupal_runtime` | Runtime | Full agentic runtime on live DDEV. **Enable with** `evaluation.enable_runtime_tasks: true` |

## Quality Gates (Contributors)

Code changes require the unit gates to pass before committing:

```bash
poetry run pytest -q tests/unit
poetry run ruff check src tests
poetry run mypy src
```

Runtime smoke additionally requires Docker + DDEV. See [AGENTS.md](../AGENTS.md#quality-gates) for the full procedure and sequential-DDEV constraint.

## Configuration

Copy `nichebench.sample.yml` → `nichebench.yml`. See [Architecture](./architecture/index.md) for full config reference.

Precedence: **CLI args > env vars > profile > defaults**

## Submodules

| Submodule | Repo | Purpose |
|---|---|---|
| `src/nichebench/frameworks/drupal/data` | `nichebench-data-drupal` | Quiz / code_gen / bug_fixing tasks |
| `src/nichebench/frameworks/drupal_runtime/data` | `nichebench-drupal-runtime-pack` | Runtime task manifests, checks, scripts |

Clone with `git clone --recursive` or `git submodule update --init --recursive`.
