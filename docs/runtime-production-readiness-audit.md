# Runtime Production Readiness Audit (Last 10 Commits)

Scope: `git log -10` ending at current `HEAD`.

## 1) Touched-files inventory (runtime-relevant)

Primary hotspots:
- `src/nichebench/core/executor.py`
- `src/nichebench/core/workspace.py`
- `src/nichebench/providers/litellm_client.py`
- `src/nichebench/config/nichebench_config.py`
- `tests/unit/core/test_cage_mode.py`
- `tests/unit/core/test_trajectory_capture.py`
- `tests/unit/core/test_workspace.py`

## 2) Dead-code audit

Findings:
- No obvious unreachable runtime branches detected in current runtime path (`cage` remains authoritative mode).
- Legacy alias normalization (`container` → `cage`) is still used and covered by tests; keep for compatibility.
- No immediate deletion candidate identified as safe-without-risk in this pass.

Action:
- Keep legacy compatibility branches but mark for future deprecation plan once config migration is complete.

## 3) Repetition patterns

Detected repetition:
- Repeated artifact write blocks in `_save_runtime_artifacts`.
- Repeated metadata/failure projection updates in `execute_runtime_test` success/error paths.
- Repeated stage evidence shape literals.

Optimization candidates:
- Introduce a small artifact writer helper map (`name -> serializer + retention policy`).
- Introduce `apply_failure_projection(result, metadata)` helper.
- Introduce stage event helper wrappers to reduce inline dict repetition.

## 4) Oversized units

Methods exceeding maintainable size thresholds:
- `TestExecutor.execute_runtime_test` (~337 lines)
- `TestExecutor._run_container_runtime_task` (~281 lines)
- `TestExecutor._build_trajectory_from_sqlite` (~250 lines)
- `JudgeRunner.evaluate_test` (~149 lines)

## 5) Minimal-diff refactor strategies

### A) `execute_runtime_test`
- Extract:
  - `_run_runtime_agent_execution(...)`
  - `_run_runtime_checks_and_judge(...)`
  - `_finalize_runtime_result(...)`
- Risk: medium (critical path)
- Required checks: unit suite + runtime smoke run
- Rollback: keep existing orchestration path behind feature flag during migration

### B) `_save_runtime_artifacts`
- Extract artifact policy table and one serializer function
- Risk: low
- Required checks: `tests/unit/core/test_trajectory_capture.py`
- Rollback: retain current direct write path via fallback

### C) `_build_trajectory_from_sqlite`
- Split DB extraction, message normalization, stats assembly
- Risk: low-medium
- Required checks: `tests/unit/core/test_trajectory_capture.py`
- Rollback: preserve old function signature and route through adapter
