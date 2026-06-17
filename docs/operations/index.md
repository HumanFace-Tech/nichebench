# Operations & Maintenance

Operational guidance for NicheBench contributors and operators.

## Shelf Contents

| Page | What It Covers |
|---|---|
| [Environment Hygiene](./environment-hygiene.md) | DDEV/Docker cleanup, zombie stale-run procedures, host cleanliness |

## Quick Reference

```bash
# Run tests
poetry run pytest -q tests/unit

# Lint + type check
poetry run ruff check src tests
poetry run mypy src

# Maintenance scripts
python scripts/runtime_maintenance.py cleanup-workspaces --dry-run
python scripts/runtime_maintenance.py prune-docker --dry-run
```

See [Ground Rules](../../AGENTS.md#ground-rules) in `AGENTS.md` for the full list of contributor rules.
