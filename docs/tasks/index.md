# Classic Task Documentation

Non-runtime task categories for NicheBench. These tasks run without DDEV — MUT + judge only.

## Shelf Contents

| Page | What It Covers |
|---|---|
| [Quiz Tasks](./quiz-tasks.md) | Static Q&A, multiple choice, LLM-as-a-Judge |
| [Code Generation Tasks](./code-generation-tasks.md) | Single/multi-turn code generation with rubric-based judging |
| [Bug Fixing Tasks](./bug-fixing-tasks.md) | Multi-turn bug fix conversations |

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
