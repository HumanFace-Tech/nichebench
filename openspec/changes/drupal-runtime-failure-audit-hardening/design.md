## Context

`drupal_runtime` is the benchmark-critical path for agentic evaluation in this repository. A failed run currently produces artifacts, but root cause analysis still requires manual cross-referencing across executor logs, cage output, runtime checks, and environment details. This creates long feedback loops and low confidence when upgrading providers, model wiring, runtime container images, and DDEV orchestration.

Current implementation components already present useful data (`metadata.json`, `run.log`, `checks.json`, optional static-analysis JSON), but there is no explicit stage contract, no stable failure taxonomy, and no operator-grade diagnosis workflow for fast incident handling.

Constraints:
- Keep backward compatibility for existing runtime task manifests.
- Preserve current scoring semantics unless explicitly changed.
- Avoid introducing required third-party services.
- Keep runtime artifacts safe for public benchmark publication (no secrets).

Stakeholders:
- Runtime framework maintainers
- Task authors
- Benchmark operators
- External users consuming published benchmark results

## Goals / Non-Goals

**Goals:**
- Define a normative A→Z runtime trace model with explicit stage boundaries and required evidence per stage.
- Standardize runtime failure codes/classes with deterministic classification rules.
- Require sufficient artifact capture so one failed run is diagnosable without rerun.
- Establish a two-phase diagnostics playbook (fast triage + deep diagnosis).
- Add hardening gates that prevent regressions in runtime execution quality.

**Non-Goals:**
- Rewriting the entire runtime engine architecture.
- Changing benchmark scoring policy weights in this change.
- Solving every model/provider behavior issue in one release.
- Introducing mandatory observability infrastructure outside repository artifacts.

## Decisions

### Decision 1: Introduce a stage-based runtime lifecycle contract
- **Choice:** Define canonical stages (config resolution, workspace/materialization, environment bootstrap, agent execution, deterministic checks, judge/scoring, artifact finalize, cleanup) and require each stage to emit start/end status plus minimal evidence.
- **Rationale:** Makes failures localizable and reproducible, enables deterministic ownership mapping.
- **Alternatives considered:**
  - Free-form logging only: rejected (inconsistent and difficult to parse).
  - Single global success/failure flag: rejected (insufficient diagnosis fidelity).

### Decision 2: Add stable failure taxonomy and failure fingerprinting
- **Choice:** Every failed runtime run must map to exactly one primary failure class/code and may include secondary contributors; fingerprint includes class, stage, command/tool context, and normalized error signature.
- **Rationale:** Supports trend analysis, rapid triage, and regression alerts.
- **Alternatives considered:**
  - Message-text matching without classes: rejected (fragile and non-actionable).
  - Multi-primary classes: rejected (ambiguous operational ownership).

### Decision 3: Extend artifact requirements instead of introducing external telemetry
- **Choice:** Keep diagnostics fully in run artifacts (JSON + logs + summary index).
- **Rationale:** Works offline, keeps reproducibility, no external infra lock-in.
- **Alternatives considered:**
  - External tracing backend: rejected for now (operational overhead and deployment complexity).

### Decision 4: Add operator playbook as first-class deliverable
- **Choice:** Define required fast-triage and deep-diagnosis workflows and bind them to failure taxonomy.
- **Rationale:** Enterprise readiness requires repeatable incident response, not ad hoc debugging.
- **Alternatives considered:**
  - Rely on maintainers’ tribal knowledge: rejected (non-scalable).

### Decision 5: Gate runtime changes with hardening quality checks
- **Choice:** Require pre-merge checks for observability invariants (stage contract completeness, failure classification determinism, artifact schema validity, cleanup guarantees).
- **Rationale:** Prevents silent quality drift as runtime stack evolves.
- **Alternatives considered:**
  - Optional best-effort gates: rejected (insufficient for production reliability).

## Risks / Trade-offs

- **[Risk] Increased artifact size and runtime overhead** → **Mitigation:** support retention tiers with mandatory minimum diagnostics payload and optional extended payload.
- **[Risk] Misclassification of failures in early versions** → **Mitigation:** include `classification_confidence` and fallback `unknown` class with explicit review queue.
- **[Risk] Added implementation complexity in executor paths** → **Mitigation:** isolate stage-trace and taxonomy logic behind dedicated internal modules/functions with unit tests.
- **[Risk] Compatibility drift across OpenCode/provider protocol changes** → **Mitigation:** add protocol contract tests and pinned compatibility matrix in CI.
- **[Risk] Sensitive data leakage in logs/artifacts** → **Mitigation:** explicit redaction policy and denylist for secrets before artifact write.

## Migration Plan

1. Introduce stage contract and taxonomy data structures behind feature flags/default-safe behavior.
2. Emit new trace/taxonomy artifacts in parallel with current artifacts.
3. Add report rendering support for new fields; keep old report behavior intact.
4. Enable hardening gates in CI as warning mode, then enforce after stabilization window.
5. Document operator playbook and update runtime authoring docs.
6. Rollback strategy: disable new diagnostics emission via config and continue existing runtime execution/scoring path.

## Open Questions

- Should failure taxonomy be versioned independently from runtime code for backward-compatible analytics?
- What is the strict minimum artifact set for “diagnosable failure” under `minimal` retention?
- Do we need per-provider protocol adapters in the trace contract for responses/chat-completions divergence?
- Should quality gates block on all failure classes initially, or only high-severity classes first?
