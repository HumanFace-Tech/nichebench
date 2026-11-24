# Runtime gate policy

> **Related:** [Reporting & Scoring](./runtime-reporting-scoring.md) | [Task Authoring](./runtime-task-authoring.md)

This document describes the interim gate policy for the current Drupal runtime task-pack definitions. A critical check can force a task result to fail regardless of its hybrid score. The policy narrows which check types can do that; it does not remove those checks from deterministic scoring.

## Gate limitations

Critical checks can fail because an observation is brittle or unavailable, not only because the
requested behavior is wrong. For example, a missing command observation or an unmatched message
pattern does not by itself establish that user-facing behavior failed. Treat these checks as signals,
not proof of functional correctness.

## Interim policy

For the current task-pack definitions:

- Structural regex checks, static-analysis checks, and required-command observations remain numerically scored but are not critical gates.
- Previously critical `pass_to_pass` checks and allowed-path checks remain critical. Watchdog checks remain noncritical. A critical failure can still force `passed=false`, independent of the hybrid score.
- The deterministic predicate/count, judge rubric, deterministic and judge weights, hybrid calculation, and threshold are unchanged.

The remaining critical checks are guardrails, not proof that the requested behavior works. The task pack currently has no assertion-style behavioral probes that establish the requested Drupal functionality directly. Check definitions may change as such probes are added and validated.

### Required-command criticality (warn predicate)

`required_command` checks are no longer uniformly critical. A `checks/<id>.yaml` sidecar can set a `required_command_criticality` mapping that marks specific commands advisory (`critical: false`). In `src/nichebench/execution/runtime/checks.py`, `load_runtime_checks` reads that mapping and overrides the default `critical: true` per command. An advisory failure no longer forces `passed=false`; it still lowers the deterministic score. Reclassifying a check can change the verdict, but not its numeric score or inclusion in the average. Do not compare pass rates before and after the reclassification without accounting for it.

## Limitations

Historical result files retain the gate semantics used when they were produced. Do not reinterpret their `passed` values using this interim policy or compare old and new pass rates without accounting for the policy change.

See [Reporting & Scoring](./runtime-reporting-scoring.md) for how critical checks affect the final result and how all deterministic checks contribute to the score.
