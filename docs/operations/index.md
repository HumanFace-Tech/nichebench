# Operations & Maintenance

Operational guidance for NicheBench contributors and operators.

## Shelf Contents

| Page | What It Covers |
|---|---|
| [Environment Hygiene](./environment-hygiene.md) | DDEV/Docker cleanup, zombie stale-run procedures, host cleanliness |

## Quick Reference

### Unit Quality Gates (no Docker/DDEV required)

```bash
poetry run pytest -q tests/unit
poetry run ruff check src tests
poetry run mypy src
```

All three must pass before committing. No external services or container runtime required.

### Runtime Smoke (requires Docker + DDEV)

Runtime tasks require a Docker-capable host with DDEV installed. See [Runtime Smoke Tests](../runtime/runtime-smoke-tests.md) for the full procedure.

> **Sequential DDEV constraint:** Only one DDEV project can be active per host at a time. Parallel runtime runs are not supported and will produce resource conflicts.

```bash
# Maintenance scripts
python scripts/runtime_maintenance.py cleanup-workspaces --dry-run
python scripts/runtime_maintenance.py prune-docker --dry-run
```

See [Ground Rules](../../AGENTS.md#ground-rules) in `AGENTS.md` for the full list of contributor rules.
