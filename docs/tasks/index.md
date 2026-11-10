# Classic Task Documentation

Non-runtime task categories for NicheBench. These tasks run without DDEV. Quiz, code generation, and bug fixing use an LLM judge.

## Shelf Contents

| Page | What It Covers |
|---|---|
| [Quiz Tasks](./quiz-tasks.md) | LLM-judged multiple choice with a parser audit cross-check |
| [Code Generation Tasks](./code-generation-tasks.md) | Single-turn code generation with direct rubric judging |
| [Bug Fixing Tasks](./bug-fixing-tasks.md) | Single-shot fixes with a finalization nudge |

## Task Categories

| Category | Type | Framework |
|---|---|---|
| `quiz` | Classic | Drupal |
| `code_generation` | Classic | Drupal |
| `bug_fixing` | Classic | Drupal |
| `drupal_runtime` | Runtime | Drupal (DDEV) |

See [Runtime Documentation](../runtime/index.md) for `drupal_runtime`.

## Running Classic Tasks

```bash
poetry run nichebench run drupal quiz
poetry run nichebench run drupal code_generation
poetry run nichebench run drupal bug_fixing
```
