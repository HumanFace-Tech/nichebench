# Cross-Model Runtime Validation Matrix — `drupal_runtime_001`

## Trial Results Summary

| Model | Det Score | Judge Score | Hybrid Score | CV | Fingerprint Stability | Failure Class |
|---|---|---|---|---|---|---|
| `groq/openai/gpt-oss-20b` | 0.286 (stable) | 0.080–0.120 | 0.183–0.203 | 6.1% | ✅ 1/3 unique | `deterministic_checks` |
| `groq/openai/gpt-oss-120b` | 0.286 (stable) | 0.000–0.120 | 0.143–0.203 | 17.3% | ✅ 1/3 unique | `deterministic_checks` |
| `llamacpp/qwen3.5-9b` | 0.286 (stable) | 0.000–0.080 | 0.143–0.183 | 13.6% | ✅ 1/3 unique | `deterministic_checks` |
| `llamacpp/qwen3.6-35b-a3b` | 0.286 (stable) | 0.000–0.080 | 0.143–0.183 | 13.6% | ✅ 1/3 unique | `deterministic_checks` |

## Key Findings

### Consistent across all models
- Deterministic score is perfectly stable (6/21 checks passing = 0.286)
- All models fail the same 15 checks — task is genuinely hard, not a model bug
- Failure fingerprint is identical across trials for each model
- Failure class is `deterministic_checks` (critical check failure) for all models
- Stage of failure: agent completes, all harness stages pass, deterministic checks fail

### Per-model observations
- **gpt-oss-20b**: Judge 0.08–0.12, hybrid 0.183–0.203, CV=6.1% (most stable)
- **gpt-oss-120b**: Judge 0.00–0.12, hybrid 0.143–0.203, CV=17.3% (highest variance — trial 3 judge=0)
- **qwen3.5-9b**: Judge 0.00–0.08, hybrid 0.143–0.183, CV=13.6% (consistent with qwen3.6)
- **qwen3.6-35b-a3b**: Judge 0.00–0.08, hybrid 0.143–0.183, CV=13.6% (identical to qwen3.5-9b)

## Shared Root Cause

All 4 models fail the same 15 checks — the agent does not complete the full Application entity implementation:
- `ContentEntityType` not created
- Routing files not written
- Services not defined
- Queue worker not created
- Custom access checker not written
- Duplicate prevention not implemented
- Mail system not configured
- Config sync out of sync
- phpstan command fails
- Drush commands not observed in run log

This means the task itself may be too large/complex for a single-agent pass without iteration, OR the task branch content needs improvement. The consistent 6/21 baseline across all models confirms harness is working correctly.

## Readiness Status

| Model | Ready? | Blocker |
|---|---|---|
| `groq/openai/gpt-oss-20b` | ✅ | None — harness stable |
| `groq/openai/gpt-oss-120b` | ✅ | None — harness stable, minor judge variance |
| `llamacpp/qwen3.5-9b` | ✅ | None — harness stable |
| `llamacpp/qwen3.6-35b-a3b` | ✅ | None — harness stable |

## Recommended Actions

1. **Task difficulty review**: Consider whether `drupal_runtime_001` is appropriately scoped for single-pass evaluation, or whether intermediate steps need explicit scaffolding.
2. **Judge stability**: `gpt-oss-120b` trial-3 scored judge=0 — investigate whether this is a transient judge sampling issue or a model-specific behavior.
3. **phpstan check fix**: The `phpstan web/modules/custom/nichejobs_application` command fails with "command not defined" — this is a check definition issue, not a model issue.
