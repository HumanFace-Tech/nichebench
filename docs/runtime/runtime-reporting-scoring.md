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

The LLM judge consumes the artifact bundle and evaluates against a rubric checklist defined in the task manifest (`judge_checklist`). The judge does not execute DDEV commands directly.

### Hybrid Score

```
hybrid_score = (deterministic_weight × deterministic_score) + (judge_weight × judge_score)
```

Default weights per manifest: 50% deterministic / 50% judge. Weights are configurable per-task.

### Critical Gate

```
if any critical check failed:
    passed = False
else:
    passed = (hybrid_score >= threshold)
```

Default threshold: `0.7`

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

---

## Artifact Bundle

| Retention | Files |
|---|---|
| `minimal` | `metadata.json`, `runtime_trace.json` |
| `standard` | `minimal` + `checks.json`, `final.diff`, `run.log`, `trajectory.json`, `last_phpcs.txt`, `last_phpstan.txt`, `watchdog_errors.txt` when available |
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

---

## CSV Export

Run results are persisted as CSV under `results/drupal_runtime/runtime/<model>/<run_id>/`:

```
results/drupal_runtime/runtime/groq/llama-3.3-70b-versatile/20260615_120000/
├── runtime/
│   └── drupal_runtime_001/
│       ├── metadata.json
│       ├── runtime_trace.json
│       ├── checks.json
│       ├── final.diff
│       ├── run.log
│       ├── trajectory.json
│       ├── last_phpcs.txt
│       ├── last_phpstan.txt
│       └── watchdog_errors.txt
└── summary.csv         # Rich table row for this run
```

Raw CSVs are stored under `reports/` at the repo root. Dated curated summaries are in [docs/archive/reports/](../archive/reports/index.md).
