# NicheBench

A CLI benchmarking harness for evaluating LLMs on **framework-specific tasks**. Starting with Drupal, it supports LLM-judged quiz, code-generation, and bug-fixing tasks, configuration-driven model management, and a Rich CLI.

Provided by [HumanFace Tech](https://humanfacetech.com).

## Quick Start

```bash
poetry install
poetry run nichebench list

# Run a task
poetry run nichebench run drupal quiz
poetry run nichebench run drupal_runtime runtime --ids drupal_runtime_001

# Run with specific models
poetry run nichebench run drupal quiz --model groq/llama-3.1-8b-instant --judge openai/gpt-4o

# Run tests
poetry run pytest -q tests/unit
```

## Task Categories

| Category | Type | Notes |
|---|---|---|
| `quiz` | Classic | LLM judge selects an answer letter; parser provides an audit cross-check |
| `code_generation` | Classic | Single-turn code generation, LLM-as-a-Judge |
| `bug_fixing` | Classic | Single-shot fix plus finalization nudge |
| `drupal_runtime` | Runtime | Full agentic runtime on live DDEV |

The deprecated `code_agent` prototype has been removed. Use `drupal_runtime` for new agentic benchmark tasks. Existing result files and historical records are not removed or invalidated by this change.

**Runtime tasks are disabled by default.** Enable with `enable_runtime_tasks: true` in `nichebench.yml`.

> **Sequential DDEV constraint:** Only one DDEV project can be active per host. Parallel runtime runs are not supported.

```yaml
evaluation:
  enable_runtime_tasks: true
```

## Configuration

```bash
cp nichebench.sample.yml nichebench.yml
# Edit nichebench.yml with your API keys and preferred models
```

Precedence: **CLI args > env vars > profile > defaults**

Relevant env vars:
```
GROQ_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
NICHEBENCH_JUDGE=openai/gpt-4o
```

## Docs Shelf

| Shelf | What You'll Find |
|---|---|
| [docs/](docs/index.md) | This documentation landing page |
| [docs/architecture/](docs/architecture/index.md) | Full system architecture, ASCII diagrams, execution layers |
| [docs/runtime/](docs/runtime/index.md) | Runtime task docs: architecture, authoring, smoke tests, diagnostics, scoring |
| [docs/tasks/](docs/tasks/index.md) | Classic task docs: quiz, code generation, bug fixing |
| [docs/operations/](docs/operations/index.md) | DDEV/Docker cleanup, zombie stale-run, host hygiene |

## Private Benchmark Artifacts

Public documentation describes the benchmarking methodology and scoring, not private run outcomes. Raw results, reports, local configs, credentials, and historical internal audits are not committed; historical audits remain local-only.

## Private Test Data

Test data is stored in private Git submodules to prevent AI training contamination.

```bash
# Clone with submodules
git clone --recursive git@github.com:HumanFace-Tech/nichebench.git

# Or for existing clones
git submodule update --init --recursive
```

| Submodule | Repo | Purpose |
|---|---|---|
| `src/nichebench/frameworks/drupal/data` | `nichebench-data-drupal` | Quiz / code_gen / bug_fixing tasks |
| `src/nichebench/frameworks/drupal_runtime/data` | `nichebench-drupal-runtime-pack` | Runtime task manifests, checks, scripts |

## Scoring

- **Quiz:** `LiteLLMJudge` selects a letter from the candidate answer, and NicheBench compares it with the gold letter for a binary score. The deterministic parser runs as an audit cross-check only. Judge calls retry up to 3 times after transient or parse failures; exhausted judge errors are counted and excluded from quiz averages. `--judge` selects the quiz judge model.
- **Code generation and bug fixing:** a direct `LiteLLMJudge` call scores rubric criteria. NicheBench recomputes and clamps the score from those criteria. Judge requests get up to 3 attempts after transient or parse failures; judge errors are recorded and excluded from averages.
- **Runtime:** deterministic checks and an optional LLM judge produce a weighted hybrid score. A critical check failure sets `passed=False`; the default threshold is `0.7`.

See [docs/runtime/runtime-reporting-scoring.md](docs/runtime/runtime-reporting-scoring.md) for full detail.

## Requirements

- Python 3.13–3.14
- Poetry for dependency management
- `docker` and `ddev` for runtime tasks
