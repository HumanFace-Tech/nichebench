## 1. Runtime Trace Contract Foundation

- [x] 1.1 Define canonical runtime stage enum and stage transition schema in core runtime execution path.
- [x] 1.2 Add stage start/end emission with timestamps and status for every runtime stage.
- [x] 1.3 Add per-stage evidence payload contract (inputs, outputs, subsystem owner, error envelope).
- [x] 1.4 Write trace artifact (`runtime_trace.json`) into runtime result bundle for success and failure runs.

## 2. Failure Taxonomy and Fingerprinting

- [x] 2.1 Define failure taxonomy classes/codes and implement deterministic primary-class resolver.
- [x] 2.2 Add fallback `unknown` failure class with explicit confidence and review hint.
- [x] 2.3 Implement normalized failure fingerprint generation (class + stage + signature + context).
- [x] 2.4 Persist failure classification and fingerprint in `metadata.json` and reporting payloads.

## 3. Artifact and Reporting Hardening

- [x] 3.1 Extend runtime artifact schema docs to include trace and taxonomy fields.
- [x] 3.2 Add result summary projection for first failing stage, primary failure class, and fingerprint.
- [ ] 3.3 Add artifact redaction pass for sensitive fields before write.
- [x] 3.4 Add retention-tier rules ensuring minimum diagnosable payload under `minimal` retention.

## 4. Diagnostics Playbook

- [ ] 4.1 Author fast-triage procedure mapping failure classes to first checks and likely owners.
- [ ] 4.2 Author deep-diagnosis A→Z procedure with evidence file/field pointers and verification commands.
- [ ] 4.3 Link playbook steps to emitted artifact schema fields and runtime stage identifiers.
- [ ] 4.4 Add troubleshooting examples for model/protocol mismatch and cage/DDEV failure classes.

## 5. Hardening Gates and Regression Coverage

- [ ] 5.1 Add unit tests for stage contract completeness and invalid transition rejection.
- [ ] 5.2 Add unit tests for taxonomy determinism and fixture-based classification expectations.
- [ ] 5.3 Add integration checks for compatibility drift signals (OpenCode/provider/runtime-image assumptions).
- [ ] 5.4 Add regression checks ensuring cleanup stage completion and workspace/DDEV teardown invariants.

## 6. Rollout and Backward Compatibility

- [ ] 6.1 Implement feature-flagged rollout path for new diagnostics fields.
- [ ] 6.2 Ensure existing runtime scoring/report commands remain functional with legacy artifacts.
- [ ] 6.3 Validate end-to-end on `drupal_runtime_001` with at least one known-fail and one known-pass fixture.
- [ ] 6.4 Update release notes and operator guidance for new runtime diagnostics model.

## 7. Recent-Change Codebase Audit (Production Readiness)

- [ ] 7.1 Review the last 10 commits touching runtime-related paths and produce a touched-files inventory.
- [ ] 7.2 Run dead-code audit on touched files (unused branches/helpers/imports/flags) and document removals or deferrals.
- [ ] 7.3 Identify repetition patterns in touched files (duplicate command assembly, artifact writes, check orchestration) and propose consolidation points.
- [ ] 7.4 Detect oversized units (functions/classes with excessive length/complexity) and produce a refactor shortlist with risk/benefit.
- [ ] 7.5 For each shortlisted refactor, provide a minimal-diff strategy (scope, tests impacted, rollback plan) plus implementation-ready task breakdowns, without mandatory immediate rewrite.

## 8. End-to-End Runtime Failure Analysis Pass

- [ ] 8.1 Execute an A→Z failure walkthrough on the latest failed `drupal_runtime_001` run artifacts and classify failure by taxonomy.
- [ ] 8.2 Compare `trajectory.json` vs `runtime_trace.json` responsibilities and validate no overlap ambiguity in reporting.
- [x] 8.4 Enforce `runtime_trace.json` presence in both `minimal` and `standard` retention modes.
- [ ] 8.3 Add a final operator report template summarizing: root cause, contributing factors, remediation, and confidence level.

## 9. Cross-Model Validation Matrix (Trials 1/2/3)

- [ ] 9.1 Define validation matrix and fixed config for `drupal_runtime_001` across: `groq/openai/gpt-oss-20b`, `groq/openai/gpt-oss-120b`, `llamacpp/qwen3.5-9b`, `llamacpp/qwen3.6-35b-a3b`.
- [ ] 9.2 Run trial-1 for each model in the matrix and capture deterministic/judge outcomes plus failure classes.
- [ ] 9.3 Run trial-2 for each model in the matrix and compare variance against trial-1 (scores, failure class drift, stage drift).
- [ ] 9.4 Run trial-3 for each model in the matrix and compute stability summary (pass rate, score spread, CV, failure fingerprint consistency).
- [ ] 9.5 Produce consolidated matrix report with per-model readiness status, blockers, and required fixes before public release.
