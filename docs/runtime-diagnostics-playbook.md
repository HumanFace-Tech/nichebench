# Runtime Diagnostics Playbook

This playbook standardizes incident response for `drupal_runtime` failures.

## Phase 1 — Fast Triage (5-10 minutes)

1. Open `metadata.json` and capture:
   - `failure_class`
   - `failure_code`
   - `first_failed_stage`
   - `failure_fingerprint`
2. Open `runtime_trace.json` and identify first stage with `status: failed`.
3. Map owner:
   - `config_resolution` → config/profile owner
   - `workspace_setup` / `environment_bootstrap` / `cleanup` → runtime+DDEV owner
   - `agent_execution` → MUT/provider/proxy owner
   - `deterministic_checks` → task/checks author
   - `judge_scoring` → judge config owner
4. Capture one-line incident summary:
   - `<failure_class>/<failure_code> at <first_failed_stage> (fingerprint: <failure_fingerprint>)`

## Phase 2 — Deep Diagnosis (A→Z)

Use these artifacts in order:

1. `runtime_trace.json`
   - Validate stage ordering and stage evidence payloads.
   - Confirm upstream stages passed before failing stage.
2. `run.log` (standard/full)
   - Validate command output around failing stage.
   - Search for network/protocol/config errors.
3. `checks.json` (standard/full)
   - Inspect deterministic check failures.
   - Confirm critical check gate behavior.
4. `trajectory.json` (standard/full)
   - Agent interaction trace only.
   - Tool-call behavior and MUT response flow.
5. `final.diff` (standard/full)
   - Validate whether code changes were created before failure.

## Responsibilities: `trajectory.json` vs `runtime_trace.json`

- `trajectory.json`: model/agent conversation and tool usage.
- `runtime_trace.json`: harness lifecycle diagnostics across runtime stages.

They are complementary and should not duplicate purpose.

## Troubleshooting Examples

### Model-protocol mismatch
- Signals: `failure_class=model_protocol_compatibility`, `agent_execution` stage failed.
- Validate provider endpoint behavior against expected OpenCode protocol.
- Confirm model binding, baseURL routing, and tool-call parser flags.

### Cage/DDEV environment failure
- Signals: `failure_class=drupal_environment` or stage failure in `workspace_setup`/`environment_bootstrap`/`cleanup`.
- Validate `ddev status`, `ddev drush status`, and cleanup guarantees.
- Confirm workspace isolation and teardown behavior.

## Operator Report Template

```
Run ID: <id>
Model: <provider/model>
Failure Class: <failure_class>
Failure Code: <failure_code>
First Failed Stage: <stage>
Fingerprint: <failure_fingerprint>

Root Cause:
Contributing Factors:
Remediation:
Confidence: <high|medium|low>
```
