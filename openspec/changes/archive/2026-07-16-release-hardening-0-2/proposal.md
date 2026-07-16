## Why

NicheBench has a working runtime benchmark implementation, but its release surface is
inconsistent: CI uses obsolete commands and paths, package metadata is still pre-release,
and several public documents no longer reflect current dependencies and operations.

This change makes the next public release reproducible and maintainable without publishing
internal benchmark reports, local provider configuration, or credentials.

## What Changes

- Repair CI to install and run the repository's supported quality commands from the root.
- Release the runtime-capable package as version `0.2.0` with accurate package metadata and
  release notes.
- Normalize score-like judge values safely before classic-task categorization and aggregation.
- Restore the Rich report-view package (`cli/rich_views/reports/`) and its shared report-rendering
  tests; both display and summary aggregation reuse `execution.summary.normalize_score` so the two
  views agree on what counts as a valid score.
- Align public documentation with current dependency versions, configuration, quality gates,
  and runtime operational constraints.
- Define the boundary between public benchmark methodology and private raw run artifacts.
- Convert `BLOCKERS.md` from obsolete session history into a concise operational runbook or
  archive obsolete entries.
- Add a release-rehearsal procedure for clean installation, quality checks, CLI smoke, and
  environment-dependent runtime smoke.

## Capabilities

### New Capabilities
- `release-readiness`: Defines the public release version, quality gates, CI contract, and
  private-artifact boundary for a NicheBench release.
- `score-normalization`: Defines safe handling of score-like judge output for classic task
  summaries.

### Modified Capabilities

- None.

## Impact

- `.github/workflows/ci.yml`, `pyproject.toml`, source summary aggregation, and unit tests.
- The restored Rich report-view package (`src/nichebench/cli/rich_views/reports/`) plus shared
  score-normalization and report-rendering tests (`tests/unit/core/test_summary.py`,
  `tests/unit/cli/test_report_rendering.py`).
- README and architecture/runtime/operations documentation, plus release notes and blocker
  documentation.
- Private Drupal runtime-pack readiness is an external prerequisite; the public harness does not
  modify pack bootstrap behavior.
- No benchmark result, private configuration, credential, or raw report becomes tracked.
