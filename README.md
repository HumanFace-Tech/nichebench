# NicheBench

NicheBench is a flexible, extensible CLI framework for benchmarking AI models on **framework-specific tasks**. Starting with Drupal, it features LLM-as-a-Judge evaluation, dynamic checklists, and a rich CLI for interactive reporting.

## Features

- **Framework Packs:** Plug-and-play support for frameworks (Drupal, WordPress, etc.)
- **Task Types:** Quiz (MCQ), code generation, and bug fixing
- **LLM-as-a-Judge:** All tasks are scored by a second LLM using custom prompts
- **Dynamic Checklists:** Each test case can define its own evaluation criteria
- **Auto-Discovery:** New frameworks and tasks are auto-discovered from `frameworks/`
- **Rich CLI:** Beautiful output with tables, panels, and progress bars
- **Parallel Execution:** Multi-process runs with live progress
- **Provider Agnostic:** Uses `litellm` for OpenAI, Groq, Anthropic, etc.

## Quick Start

```bash
# Install dependencies and activate environment
poetry install
poetry shell

# List available frameworks and tasks
nichebench list

# View specific tasks for a framework
nichebench list-tasks drupal

# Inspect a test case
nichebench show drupal_quiz_001

# Run evaluations (stub mode for now)
nichebench run drupal quiz --model gpt-4

# View results
nichebench report

# Alternative: Run without activating shell
poetry run nichebench list
```

## Current Status

- **Drupal Framework Pack:** 9 tasks (5 quiz, 3 code generation, 1 bug fixing)
- **Rich CLI:** Interactive listing, task inspection, and result reporting
- **Stub Runner:** Basic evaluation flow with dummy results
- **Results Storage:** Structured JSON/JSONL in `results/` directory

## Project Structure

```text
nichebench/
├── results/                # Run outputs
└── src/
    └── nichebench/
        ├── cli/            # CLI + Rich UI
        ├── core/           # Discovery, datamodel, loaders
        ├── providers/      # LLM client + judge adapters
        ├── frameworks/     # Framework packs (Drupal, ...)
        ├── config/         # Settings
        └── utils/          # Helpers
```

## Development

- Python 3.10+, Poetry, Typer, Rich, deepeval, litellm
- Test with `pytest -n auto`
- Follow PEP8 and project linting rules

## How NicheBench Differs

- Judge-based evaluation (no regex)
- Modular, framework-specific, checklist-driven
- Not a generic eval harness

## License

MIT
