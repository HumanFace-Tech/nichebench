# Runtime Diagnostics Playbook

> **Related:** [Architecture & Lifecycle](./runtime-architecture-lifecycle.md) | [Task Authoring](./runtime-task-authoring.md) | [Smoke Tests](./runtime-smoke-tests.md)

---

This playbook standardizes incident response for `drupal_runtime` failures.

## Runtime Topology

```text
host NicheBench process
  │
  |-- creates workspaces/run-<task>-<uuid>/
  |-- starts isolated DDEV project nb-<task>-<uuid>
  |-- writes TASK.md, opencode.json, optional HINTS.md
  │
  `-- docker run OpenCode cage
        │
        │ mounts workspace at the same host path
        │ mounts /nichebench/islands/input (read-only)
        │ mounts /nichebench/islands/output-trace
        │ mounts /nichebench/state/* for isolated OpenCode state
        ` uses Docker socket for DDEV commands
```

## Artifact Flow

```text
OpenCode run.log + trajectory
        │
workspace final state -- git diff --> final.diff
        │
deterministic checks -----------> checks.json
        │                           │
        │                           +--> last_phpcs.txt / last_phpstan.txt / watchdog_errors.txt
        v
metadata + runtime_trace + artifacts --> LLM judge context --> final/hybrid score
```

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
6. `last_phpcs.txt`, `last_phpstan.txt`, `watchdog_errors.txt` (standard/full when available)
   - Inspect focused validation failure details without searching the full run log.

## Responsibilities: `trajectory.json` vs `runtime_trace.json`

- `trajectory.json`: model/agent conversation and tool usage.
- `runtime_trace.json`: harness lifecycle diagnostics across runtime stages.

They are complementary and should not duplicate purpose.

## Forensics Output: Reasoning vs Text vs Tool Counts

`forensics` reports (`nichebench forensics --path <trial_dir>`) now expose four
extra trajectory metrics alongside `tool_calls_total`:

| Field | Source part type | What it tells you |
|---|---|---|
| `reasoning_total` | `type=reasoning` part count | How many chain-of-thought turns the agent emitted |
| `reasoning_chars` | Sum of `part.text` for reasoning parts | Total reasoning volume (signals "thinking budget" consumed) |
| `text_replies_total` | `type=text` part count | How many visible user-facing replies the agent produced |
| `text_chars` | Sum of `part.text` for text parts | Total visible reply volume |

In the OpenCode UI these are the collapsed "thinking" blocks and the visible
final reply. The split is useful when triaging "no visible output" failures:
a high `reasoning_chars` with a low `text_chars` ratio usually means the
agent thought but did not answer. The same fields are also present in the
JSON output (`--json`) for downstream tooling.

## Troubleshooting Examples

### Model-protocol mismatch
- Signals: `failure_class=model_protocol_compatibility`, `agent_execution` stage failed.
- Validate provider endpoint behavior against expected OpenCode protocol.
- Confirm model binding, baseURL routing, and tool-call parser flags.

### Cage/DDEV environment failure
- Signals: `failure_class=drupal_environment` or stage failure in `workspace_setup`/`environment_bootstrap`/`cleanup`.
- Validate `ddev status`, `ddev drush status`, and cleanup guarantees.
- Confirm workspace isolation and teardown behavior.

### Agent stopped but process never exited
- Signals: `failure_class=agent_execution`, code `agent.did_not_exit`.
- Meaning: OpenCode produced assistant `finish=stop`, then no further DB activity before watchdog threshold.
- Artifacts to inspect:
  - `opencode_partial_trajectory.json`
  - `opencode_session_dump.json`
  - `run.log` (partial timeout/stall tail)
- Typical remediation:
  1. Verify provider endpoint health (`/v1/models`, lightweight `/v1/responses` probe).
  2. Check recent tool-call pattern for low-novelty loops.
  3. Reduce context/compaction drift or increase watchdog idle threshold if legitimately long finalization is expected.

### Agent execution stalled (no activity)
- Signals: `failure_class=agent_execution`, code `agent.execution_stalled`.
- Meaning: no new OpenCode message/part activity within inactivity watchdog threshold.
- Artifacts to inspect:
  - `runtime_trace.json` (`agent_execution` evidence error)
  - `opencode_session_dump.json` (last event/message timestamp)
  - `run.log` partial output
- Typical remediation:
  1. Verify provider latency and model readiness.
  2. Confirm DB/session writes are still progressing during long runs.
  3. Tune `runtime_watchdog_inactivity_seconds` and `runtime_watchdog_poll_seconds` conservatively.

## Failure Report Template

Use this template when filing a runtime incident report:

```
## Run summary
- Run ID:
- Timestamp:
- Model:
- Runtime mode:
- Retention mode:

## Failure classification
- Failure class:
- Failure code:
- First failed stage:
- Failure fingerprint:
- Classification confidence:

## Root-cause analysis
- Root cause:
- Contributing factors:
- Evidence used (runtime_trace.json, metadata.json, run.log, checks.json, trajectory.json):

## Remediation
- Immediate fix:
- Follow-up hardening:
- Owner:
- ETA:

## Confidence
- Confidence level: high / medium / low
- Remaining uncertainty:
```

## Operator Quick Report

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
