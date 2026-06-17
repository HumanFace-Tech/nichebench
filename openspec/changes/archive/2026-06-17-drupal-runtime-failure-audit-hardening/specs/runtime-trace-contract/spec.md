## ADDED Requirements

### Requirement: Canonical Runtime Stage Contract
The runtime execution pipeline SHALL emit a canonical stage contract covering configuration resolution, workspace setup, environment bootstrap, agent execution, deterministic checks, judge/scoring, artifact finalization, and cleanup.

#### Scenario: Stage boundaries are emitted
- **WHEN** a runtime run starts
- **THEN** the system records an ordered stage list with start/end timestamps and per-stage status

### Requirement: Stage Evidence Payload
Each stage SHALL emit a minimum evidence payload containing stage identifier, runtime context, inputs consumed, outputs produced, and failure details if status is failed.

#### Scenario: Failed stage has actionable evidence
- **WHEN** a stage transitions to failed
- **THEN** the evidence payload includes command/tool context, normalized error fields, and owning subsystem identifier

### Requirement: Trace Artifact Availability
The runtime result bundle SHALL include a machine-readable trace artifact that can reconstruct the full A→Z lifecycle without parsing free-form logs.

#### Scenario: Trace can be consumed directly
- **WHEN** an operator inspects a failed run
- **THEN** they can determine the first failing stage and upstream/downstream stage outcomes from the trace artifact alone
