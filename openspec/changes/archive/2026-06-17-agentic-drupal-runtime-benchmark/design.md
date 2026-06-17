## Context

NicheBench currently evaluates mostly one-shot outputs (quiz/code generation/bug-fix prompts) and uses DeepEval-compatible wrappers for rubric scoring. This does not measure realistic Drupal agent behavior, where an agent must inspect a full project, run `ddev` and `drush`, execute tests, and produce reviewable changes.

The target state is a runtime benchmark architecture with branch-based task baselines (`seed/main`, `task/*`) and run-time commit freezing (`resolved_sha`) so maintenance remains practical while official results remain reproducible.

Primary constraints:
- Maintain compatibility with existing NicheBench control plane and reporting.
- Support complex Drupal setups (many contrib modules, custom modules, large config).
- Keep task data and fixture complexity in a separate task-pack repository.
- Allow controlled capability profiles (offline, web-enabled, optional browser automation).

Stakeholders:
- Benchmark maintainers (task authoring, baseline maintenance, CI health)
- Model evaluators (consistent scoring, comparable runs)
- Agent developers (realistic runtime signals, reproducible failures)

## Goals / Non-Goals

**Goals:**
- Add runtime execution capability for Drupal tasks with DDEV-based workflows.
- Define a runtime manifest contract for environment setup, checks, budgets, tool profile, and deliverables.
- Implement branch-first baseline management while freezing each run to `resolved_sha`.
- Score with deterministic checks as primary and optional LLM judge as secondary.
- Provide branch synchronization automation from `seed/main` to `task/*` and conflict visibility.

**Non-Goals:**
- Replacing existing one-shot benchmark categories.
- Requiring a single agent framework (LangGraph is not mandatory).
- Building full cloud orchestration in v1.
- Making LLM-judge output the sole pass/fail oracle.

## Decisions

### Decision 1: Two-repo architecture (control-plane + task-pack)
Use `nichebench` for orchestration/scoring and `drupal-runtime-pack` for heavy fixture/task assets.

Rationale:
- Keeps benchmark runtime logic decoupled from large fixture history.
- Enables independent lifecycle/versioning for task content.
- Preserves clean separation between platform and dataset.

Alternatives considered:
- Single monorepo: simpler startup, but quickly becomes noisy and heavy.

### Decision 2: Branch-first authoring, commit-freeze execution
Task manifests reference `base_branch`; runner resolves and persists `resolved_sha` at run start.

Rationale:
- Supports practical branch maintenance and shared seed updates.
- Maintains scientific reproducibility for official runs.

Alternatives considered:
- SHA-only manifests: reproducible but high maintenance friction for evolving tasks.

### Decision 3: Runtime manifest contract as source of truth
Introduce `task_type: runtime` manifest fields for source branch, setup mode, checks, tool profile, budgets, and required artifacts.

Rationale:
- Gives deterministic and testable task contracts.
- Makes runner behavior explicit and auditable.

Alternatives considered:
- Hidden per-task scripts only: flexible but opaque and harder to validate consistently.

### Decision 4: Deterministic-first hybrid scoring
Use deterministic checks as primary gate and weighted base score, with optional rubric scoring as secondary signal.

Rationale:
- Prevents subjective judge drift from dominating outcomes.
- Retains qualitative signal for maintainability/convention alignment.

Alternatives considered:
- Judge-only scoring: easier to implement but weaker reproducibility.

### Decision 5: Tool-access profiles as first-class benchmark dimension
Define explicit profiles (`offline_cli`, `web_cli`, `web_plus_browser`) and independent feature flags.

Rationale:
- Enables fair capability-sliced comparisons.
- Makes policy and network assumptions explicit per run.

Alternatives considered:
- One fixed profile: simpler but prevents controlled experiments.

## Risks / Trade-offs

- [DDEV startup and environment flakiness] -> Use health checks, retries, and strict teardown; keep deterministic pre-flight checks.
- [Branch drift can reduce comparability] -> Persist `resolved_sha` and require frozen refs for official leaderboard runs.
- [Seed updates can produce merge conflicts across tasks] -> Add sync pipeline with conflict reporting and manual maintenance queue.
- [Complex fixtures increase runtime cost] -> Introduce step/time budgets and profile-based test subsets.
- [Web-enabled profiles increase variability/security surface] -> Separate profile policies and enforce run-level auditing.

## Migration Plan

1. Add runtime schema and runner wiring in control plane (without replacing existing categories).
2. Create task-pack repository with one realistic seed branch and initial `task/*` branches.
3. Implement runtime execution loop (workspace prep -> `ddev start` -> agent run -> artifact capture -> checks).
4. Add branch resolution metadata (`base_branch`, `resolved_sha`) to run outputs and reports.
5. Add branch sync automation from `seed/main` to `task/*` with conflict status publication.
6. Roll out first 5 runtime tasks and validate run reproducibility on frozen refs.

Rollback:
- Disable `task_type: runtime` and keep legacy one-shot paths active.
- Keep runtime scoring and runner behind feature toggle until stable.

## Open Questions

- Should official benchmark batches use tags (`freeze/<batch>/<task>`) or a generated lockfile of task->SHA mappings?
- What is the default policy for shell network access in `web_cli` (full internet vs curated allowlist)?
- Should Playwright/browser tooling be included in v1 profile set or staged into v2?
- What is the preferred sync strategy per branch (`merge`, `rebase`, or hybrid by task complexity)?
