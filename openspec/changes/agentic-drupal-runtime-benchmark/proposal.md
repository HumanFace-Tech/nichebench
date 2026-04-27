## Why

NicheBench currently benchmarks mostly one-shot prompt outputs, which does not reflect how modern coding agents solve real Drupal work. We need a runtime benchmark where agents operate on a realistic Drupal project, run developer tooling, and are scored with deterministic checks plus rubric signals.

## What Changes

- Add a new runtime benchmark mode for Drupal tasks that executes agents against full project workspaces (not synthetic snippets).
- Introduce branch-first task baselines (`seed/main`, `task/*`) with run-time commit freeze (`resolved_sha`) for reproducible scoring.
- Add task manifests for runtime scenarios, including environment setup mode, checks, budgets, tool profile, and deliverables.
- Add deterministic evaluation flow (fail-to-pass, pass-to-pass, required commands, path policy) with optional LLM judge weighting.
- Add tool-access profiles to compare model behavior under different capability constraints (`offline_cli`, `web_cli`, `web_plus_browser`).
- Add branch maintenance automation to propagate seed updates into task branches and report conflicts.

## Capabilities

### New Capabilities
- `drupal-runtime-task-execution`: Run benchmark tasks against realistic Drupal workspaces with DDEV, drush, tests, and captured artifacts.
- `branch-based-task-baselines`: Define and maintain task baselines as branches while freezing each run to a resolved commit SHA.
- `runtime-task-manifest-schema`: Define runtime task metadata for environment setup, checks, budgets, tool access, and outputs.
- `hybrid-runtime-scoring`: Score runtime tasks with deterministic checks as primary and optional LLM judge as secondary.
- `tool-access-profiles`: Configure benchmark runs with explicit capability profiles and feature flags for fair comparisons.

### Modified Capabilities
- None.

## Impact

- Affects runtime orchestration and evaluation flow in `src/nichebench/core/` and `src/nichebench/metrics/`.
- Adds new runtime task data and specs under OpenSpec change artifacts.
- Introduces DDEV-driven environment assumptions for runtime tasks.
- Expands reporting to include branch/commit provenance, artifact logs, and deterministic check outcomes.
