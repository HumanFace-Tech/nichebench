## 1. Runtime Schema and Validation

- [x] 1.1 Add `task_type: runtime` schema support with required fields for `source`, `environment`, `agent`, `checks`, `scoring`, and `deliverables`
- [x] 1.2 Implement manifest validation errors for missing/invalid runtime fields
- [x] 1.3 Add setup mode validation for `config_import` and `db_snapshot`

## 2. Branch Baseline Resolution and Provenance

- [x] 2.1 Implement `base_branch` resolution at run start to capture `resolved_sha`
- [x] 2.2 Persist `base_branch` and `resolved_sha` in run metadata and report outputs
- [x] 2.3 Add official-run guard that rejects missing frozen provenance metadata

## 3. Runtime Execution Harness

- [x] 3.1 Implement isolated runtime workspace creation per task run
- [x] 3.2 Integrate DDEV lifecycle management (`start`, health-check, teardown)
- [x] 3.3 Wire agent execution loop for OpenCode against runtime workspaces
- [x] 3.4 Capture required artifacts (git diff/patch, command logs, check results)

## 4. Deterministic Checks and Hybrid Scoring

- [x] 4.1 Implement deterministic check runner for fail-to-pass, pass-to-pass, required commands, and path policy
- [x] 4.2 Implement deterministic pass gate that blocks overall `pass` on critical check failure
- [x] 4.3 Implement weighted hybrid score composition with optional judge contribution
- [x] 4.4 Add deterministic-only fallback mode when judge scoring is disabled/unavailable

## 5. Tool Access Profiles

- [x] 5.1 Implement profile presets (`offline_cli`, `web_cli`, `web_plus_browser`) and effective flag resolution
- [x] 5.2 Enforce profile-specific tool restrictions during runtime execution
- [x] 5.3 Record effective tool profile and flags in run metadata/artifacts

## 6. Branch Sync Automation

- [x] 6.1 Add CI workflow to propagate `seed/main` updates into `task/*` branches
- [x] 6.2 Publish branch sync/conflict status for maintainers
- [x] 6.3 Add optional auto-PR generation for branches requiring manual conflict resolution

## 7. Reporting and Developer UX

- [x] 7.1 Extend reports to include runtime artifact pointers and provenance metadata
- [x] 7.2 Add CLI output sections for deterministic check breakdown and hybrid score components
- [x] 7.3 Document runtime task authoring workflow and branch maintenance conventions

## 8. Validation and Rollout

## 8. Job Runtime Isolation and DDEV Execution

- [x] 8.1 Select and pin OpenCode runtime image strategy (official/custom), including immutable image tag/digest policy
- [x] 8.2 Implement job-container runner that executes OpenCode in-container with Docker socket bind for full `ddev` command support
- [x] 8.3 Define and enforce unique per-run workspace + DDEV project naming to enable safe parallel execution (5+ concurrent tasks)
- [x] 8.4 Add preflight health checks in job container (`docker version`, `ddev version`, `ddev start`, `ddev drush status`)
- [x] 8.5 Add configurable runtime limits (timeout/cpu/memory/worker concurrency) and failure-safe teardown

## 9. Runtime Security, Storage, and Cleanup

- [x] 9.1 Implement container hardening defaults (non-root user, capability drop, `no-new-privileges`, minimal writable mounts)
- [x] 9.2 Implement per-run cleanup sequence (`ddev stop --remove-data`, container removal, workspace cleanup)
- [x] 9.3 Add Docker disk-budget maintenance commands/scripts (safe prune defaults + optional aggressive maintenance mode)
- [x] 9.4 Add artifact retention policy (keep lightweight logs/diffs by default, optional keep-workspaces mode)

## 10. Artifact Contract and Judge Inputs

- [x] 10.1 Define and emit normalized runtime artifact bundle (`metadata.json`, `git-log.txt`, `final.diff`, `checks.json`, `run.log`)
- [x] 10.2 Capture consolidated diff between baseline SHA and final state (`base_sha...HEAD`) as primary judge input
- [x] 10.3 Capture commit history between baseline SHA and final state as secondary judge input
- [x] 10.4 Extend reporting to clearly separate deterministic gate outcomes from optional judge scoring outcomes
- [x] 10.5 Add optional browser-validation artifact channel (screenshots/traces) behind profile flag (no default requirement)

## 11. Validation and Rollout

- [ ] 11.1 Create and validate five initial complex runtime tasks in task-pack branches (TODO: Requires external repo)
- [ ] 11.2 Run repeatability checks on frozen runs and verify consistent outcomes (TODO: Requires external repo)
- [ ] 11.3 Benchmark profile matrix (`offline_cli`, `web_cli`, `web_plus_browser`) for at least one model (TODO: Requires live model evals)
- [x] 11.4 Enable runtime tasks behind feature flag and prepare phased rollout notes
- [ ] 11.5 Runs/benches execution + evidence capture (must be final execution step after all implementation tasks above)
