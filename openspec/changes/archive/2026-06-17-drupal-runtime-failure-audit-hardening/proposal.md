## Why

`drupal_runtime` is the canonical agentic benchmark path, but current runs still fail in ways that are hard to explain end-to-end from one place. We need a production-grade failure-audit and hardening pass now so runtime results are deterministic, diagnosable, and trustworthy for public enterprise evaluation.

## What Changes

- Add an end-to-end runtime execution trace contract that maps every stage from task selection to scoring artifacts, with explicit stage-level pass/fail signals.
- Add deterministic failure classification for runtime runs (configuration, networking, model/protocol mismatch, cage/runtime, DDEV/Drupal checks, judge/scoring, cleanup).
- Add richer artifact capture requirements so a single failed run can be diagnosed without rerunning (normalized timeline, request/response summaries, failure fingerprint).
- Add a two-step operational playbook for runtime incidents:
  - Step 1: fast triage (identify failure class and likely owner in minutes)
  - Step 2: deep diagnosis (A→Z trace and remediation checklist)
- Add optimization and quality gates for runtime maintainability (observable invariants, regression checks, and drift detection across OpenCode/llama-swap/runtime images).

## Capabilities

### New Capabilities
- `runtime-trace-contract`: Defines the authoritative A→Z runtime lifecycle, required telemetry at each stage, and expected artifacts for success/failure paths.
- `runtime-failure-taxonomy`: Defines standardized failure classes, evidence requirements, and stable failure codes for reporting and triage.
- `runtime-diagnostics-playbook`: Defines mandatory fast-triage and deep-diagnosis workflows for runtime failures.
- `runtime-hardening-gates`: Defines quality gates and regression checks that must pass before runtime pipeline changes ship.

### Modified Capabilities
- None.

## Impact

- Affected systems: runtime executor, cage/OpenCode integration, artifact generation, deterministic check orchestration, reporting UX.
- Affected code areas: `src/nichebench/core/executor.py`, runtime scoring/reporting paths, runtime artifact writers, docs for runtime authoring and operations.
- Operational impact: faster root-cause analysis, fewer opaque failures, safer upgrades to model/proxy/runtime stack.
- Dependency considerations: no immediate new external dependency is required; may introduce optional diagnostics tooling in follow-up implementation tasks.
