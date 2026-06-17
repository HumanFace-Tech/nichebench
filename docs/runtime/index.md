# Runtime Documentation

> **Start here:** [Architecture & Lifecycle](./runtime-architecture-lifecycle.md)

Canonical documentation for `drupal_runtime` agentic tasks.

## Shelf Contents

| Page | What It Covers |
|---|---|
| [Architecture & Lifecycle](./runtime-architecture-lifecycle.md) | How runtime tasks work end-to-end: stages, cage mode, islands, isolation |
| [Task Authoring](./runtime-task-authoring.md) | Writing task manifests, branch conventions, YAML schema, maintenance scripts |
| [Smoke Tests](./runtime-smoke-tests.md) | End-to-end smoke procedure, `runtime_smoke.py`, exit codes, JSON schema |
| [Diagnostics Playbook](./runtime-diagnostics-playbook.md) | Failure triage, artifact inspection order, troubleshooting examples, report template |
| [Reporting & Scoring](./runtime-reporting-scoring.md) | Deterministic %, Judge %, Hybrid %, critical gate, Rich output table, artifact inventory |

## Enable Runtime Tasks

Runtime tasks are **disabled by default**. Add to `nichebench.yml`:

```yaml
evaluation:
  enable_runtime_tasks: true
```

## Run a Runtime Task

```bash
poetry run nichebench run drupal_runtime runtime --ids drupal_runtime_001 \
  --model groq/llama-3.3-70b-versatile
```
