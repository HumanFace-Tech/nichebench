# Runtime Reporting & Scoring

> **Related:** [Architecture & Lifecycle](./runtime-architecture-lifecycle.md) | [Task Authoring](./runtime-task-authoring.md) | [Diagnostics Playbook](./runtime-diagnostics-playbook.md)

---

## Score Semantics

Runtime tasks produce three scores. The **hybrid score** is the canonical final score.

### Deterministic Score

```
deterministic_score = passed_checks / total_checks
```

Derived from deterministic checks defined in the task manifest (`checks` list). Each check is one of:

| Check type | Meaning |
|---|---|
| `fail_to_pass` | Command expected to fail before MUT, pass after |
| `pass_to_pass` | Command must continue to pass throughout |
| `path_policy` | Verifies modifications are restricted to allowed directories |

### Judge Score

```
judge_score = LLM judge rubric score (0.0 – 1.0)
```

The LLM judge consumes the artifact bundle and evaluates against a rubric checklist defined in the task manifest (`judge_checklist`). The judge does not execute DDEV commands directly. Calls retry up to three times. NicheBench recomputes the score from manifest criteria and scores each criterion at most once, so duplicate or extra judge criteria cannot inflate the result.

### Hybrid Score

```
hybrid_score = (deterministic_weight × deterministic_score) + (judge_weight × judge_score)
```

Default weights per manifest: 70% deterministic / 30% judge. Weights are configurable per-task. A failed or malformed judge response is not replaced with a deterministic-only hybrid score.

### Judge and configuration errors

After three unsuccessful attempts, judge outages or malformed output raise `JudgeError` with `judge_unavailable`, `judge_invalid_json`, or `judge_missing_criteria`. The result row records `error_kind`, sets `passed=false`, and serializes `judge_score`, `hybrid_score`, `final_score`, and `overall_score` as null. These rows count as `judge_errors` and are excluded from model averages and failed-model counts in completion headers. Completion headers show judge-error counts for quiz, code, and runtime runs.

Runtime setup, image, Docker, network, CLI, or configuration failures use `error_kind=config_error`. They count as `config_errors` and are also excluded from model averages. Reports display `ERROR`/`N/A` for these excluded rows rather than treating them as 0%.

### Critical gate

```
if any critical check failed:
    passed = False
else:
    passed = (hybrid_score >= threshold)
```

Default threshold: `0.7`. Only checks marked critical can force a failure independently of the hybrid score. For the current task-pack policy, see [Runtime Gate Policy](./runtime-gate-policy.md). A critical check is a gate signal, not by itself proof of task functionality.

---

## Rich Output Table

When a runtime task completes, NicheBench prints a Rich table to stdout:

```
┌──────────────────────────────────────────────────────────────────────┐
│  drupal_runtime_001  │  groq/llama-3.3-70b-versatile                  │
├──────────────────────────────────────────────────────────────────────┤
│  Checks      │  20/21          │  Deterministic  │  95.24%           │
│  Judge       │  0.46           │  Hybrid         │  70.62%  ✓        │
│  Critical    │  false          │  Result         │  PASS             │
└──────────────────────────────────────────────────────────────────────┘
```

Columns:

| Column | Source | Notes |
|---|---|---|
| `Checks` | `checks.json` | `passed/total` |
| `Deterministic` | `checks.json` | `passed_checks / total_checks × 100` |
| `Judge` | `judge.json` | Raw judge score |
| `Hybrid` | Computed | Weighted blend; `✓` if ≥ threshold |
| `Critical` | `checks.json` | `true` if any critical check failed |
| `Result` | Gate | `PASS` / `FAIL` |

If the row has a judge or configuration error, the report uses `ERROR`/`N/A` for unavailable scores and shows the corresponding error count. Such rows do not contribute to model averages or failed-model counts.

---

## Artifact Bundle

| Retention | Files |
|---|---|
| `minimal` | `metadata.json`, `runtime_trace.json` |
| `standard` | `minimal` + `checks.json`, `final.diff`, `run.log`, `trajectory.json`, `last_phpcs.txt`, `last_phpstan.txt`, `watchdog_errors.txt`, `opencode_partial_trajectory.json`, `opencode_session_dump.json` when available |
| `full` | `standard` + optional raw/debug/browser payloads |

### Key Artifacts

| File | Purpose |
|---|---|
| `metadata.json` | Run metadata: model, mode, island topology, failure taxonomy |
| `runtime_trace.json` | Stage-by-stage harness lifecycle trace |
| `checks.json` | Deterministic check results with pass/fail per check |
| `final.diff` | Git diff of workspace changes made by MUT |
| `run.log` | Full stdout/stderr of the runtime process |
| `trajectory.json` | Agent conversation and tool-call trace |
| `last_phpcs.txt` | PHPCS output from last static-analysis check |
| `last_phpstan.txt` | PHPStan output from last static-analysis check |
| `watchdog_errors.txt` | Drupal watchdog PHP errors captured during run |
| `opencode_partial_trajectory.json` | Partial trajectory snapshot captured on timeout/abort (optional) |
| `opencode_session_dump.json` | OpenCode session dump for forensic inspection (optional) |

---

## Results Layout

Run results are persisted as JSON under
`results/<framework>/<category>/<model>/<timestamp>-<uuid8>/`:

- The `<model>` segment is the MUT model string with `/` replaced by `-`
  (e.g. `groq/llama-3.3-70b-versatile` → `groq-llama-3.3-70b-versatile`).
- `<timestamp>` follows the configured `timestamp_format` (default
  `%Y%m%d_%H%M%S`); an 8-character UUID suffix is appended so two runs that
  share a timestamp bucket never collide on `details.jsonl` / `summary.json`.

```
results/drupal_runtime/runtime/groq-llama-3.3-70b-versatile/20260615_120000-abcd1234/
├── summary.json             # Aggregate run statistics
├── details.jsonl            # Per-test results (append-only)
└── runtime/
    └── drupal_runtime_001/  # Per-test artifact bundle (one dir per test_id)
        ├── metadata.json
        ├── runtime_trace.json
        ├── checks.json
        ├── final.diff
        ├── run.log
        ├── trajectory.json
        ├── last_phpcs.txt      # optional — only when the validation hook fires
        ├── last_phpstan.txt    # optional — only when the validation hook fires
        ├── watchdog_errors.txt # optional — only when the validation hook fires
        ├── opencode_partial_trajectory.json # optional — written on timeout/abort
        └── opencode_session_dump.json        # optional — written on timeout/abort
```

- The run root holds `summary.json` (aggregate) and `details.jsonl`
  (append-only per-test stream). There is **no** `summary.csv`; CSV export is
  not part of the persisted layout.
- Optional check-output files (`last_phpcs.txt`, `last_phpstan.txt`,
  `watchdog_errors.txt`) are emitted only when their respective validation
  hooks fire. With `runtime_artifact_retention: minimal`, only
  `metadata.json` and `runtime_trace.json` are written.
- Optional trajectory artifacts (`opencode_partial_trajectory.json`,
  `opencode_session_dump.json`) are written by the artifact writer when
  available (e.g. on timeout/abort) and require `standard` or `full`
  retention to be persisted.
- Multi-trial runs nest artifacts at `runtime/<test_id>/trial_<n>/`.

Public documentation describes reporting methodology, not private run outcomes. Raw results, reports, local configs, credentials, and historical internal audits are not committed; historical audits remain local-only.
