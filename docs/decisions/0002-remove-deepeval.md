# Decision 0002: remove DeepEval

## Status

Accepted

## Context

DeepEval added no runtime benefit to NicheBench's scoring path. Importing it triggered telemetry and network behavior, while its dependency tree increased install size and its release cadence introduced version drift.

## Decision

Remove DeepEval from source and project dependencies. Keep score types and the quiz parser in `metrics/types.py` and `metrics/quiz.py`, and rubric score handling in `metrics/rubric.py`. Quiz, code-generation, and bug-fixing evaluation use the direct `LiteLLMJudge`; the quiz parser is an audit cross-check, not a scoring authority.

## Scoring and error contract

- The judge may make up to 3 attempts after transient request failures or invalid JSON. This is the `LiteLLMJudge` default, not a configuration setting.
- NicheBench computes the reported score from criterion results: true is 1, partial is 0.5, and false or missing is 0. It clamps the result to the valid range.
- `judge_overall_score` is retained for audit. `judge_score` and `final_score` use the recomputed score.
- Failed judging is recorded as `error_kind=judge_unavailable` or `error_kind=judge_invalid_json`. These results are excluded from averages, rather than counted as zero-quality answers.

## Consequences

The scoring path has fewer dependencies and no DeepEval import-time telemetry. Criterion outcomes, rather than a judge-supplied aggregate, determine scores.

## Rollback

To restore DeepEval, reintroduce its dependency and metric adapters, then restore their tests and scoring integration. Do not mix the old and direct-judge paths in one run; changing the scoring implementation changes score comparability.
