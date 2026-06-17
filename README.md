# NicheBench

A CLI benchmarking harness for evaluating LLMs on **framework-specific tasks**. Starting with Drupal, it features LLM-as-a-Judge evaluation, configuration-driven model management, and a Rich CLI for interactive reporting.

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
| `quiz` | Classic | Static Q&A, LLM-as-a-Judge |
| `code_generation` | Classic | Single/multi-turn code gen, LLM-as-a-Judge |
| `bug_fixing` | Classic | Multi-turn bug fix conversation |
| `drupal_runtime` | Runtime | Full agentic runtime on live DDEV |

**Runtime tasks are disabled by default.** Enable with:

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
| [docs/archive/reports/](docs/archive/reports/index.md) | Dated audit snapshots (not canonical) |

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

- **Deterministic** = passed checks / total checks
- **Judge** = LLM judge evaluating artifact bundle
- **Hybrid** = weighted blend (default 50/50 per manifest)
- Any critical check failure → `passed=False`
- Default threshold: `0.7`

See [docs/runtime/runtime-reporting-scoring.md](docs/runtime/runtime-reporting-scoring.md) for full detail.

## Requirements

- Python 3.10+
- Poetry for dependency management
- `docker` and `ddev` for runtime tasks
