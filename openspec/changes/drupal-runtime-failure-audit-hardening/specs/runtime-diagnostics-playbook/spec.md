## ADDED Requirements

### Requirement: Two-Phase Diagnostics Workflow
Runtime operations SHALL define a two-phase diagnostics workflow: fast triage and deep diagnosis.

#### Scenario: Fast triage path is available
- **WHEN** a runtime run fails
- **THEN** operators can identify likely failure class, failing stage, and probable owner within a bounded triage workflow

### Requirement: Deep Diagnosis Trace Procedure
The deep diagnosis workflow SHALL provide an A→Z trace procedure that maps each stage to required evidence, verification commands, and remediation hints.

#### Scenario: Deep diagnosis can reproduce root cause
- **WHEN** fast triage is insufficient
- **THEN** operators can follow deep diagnosis steps to isolate root cause without relying on undocumented tribal knowledge

### Requirement: Playbook-Artifact Alignment
Diagnostics workflow steps SHALL reference concrete runtime artifacts and schema fields rather than unstructured log scraping alone.

#### Scenario: Playbook references stable fields
- **WHEN** an operator executes diagnostics steps
- **THEN** each step points to explicit artifact files and field paths required for decision making
