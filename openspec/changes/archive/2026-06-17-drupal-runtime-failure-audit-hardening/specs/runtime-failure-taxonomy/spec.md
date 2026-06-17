## ADDED Requirements

### Requirement: Deterministic Failure Classification
Every failed runtime run SHALL be assigned exactly one primary failure class and failure code from a maintained taxonomy.

#### Scenario: Primary class is assigned
- **WHEN** runtime execution ends in failure
- **THEN** the result metadata includes one primary failure class and code

### Requirement: Failure Class Coverage
The taxonomy SHALL include at minimum configuration, network/connectivity, model-protocol compatibility, cage/runtime execution, DDEV/Drupal environment, deterministic checks, judge/scoring, and cleanup classes.

#### Scenario: Failure class maps to known domain
- **WHEN** a failure is produced by any runtime stage
- **THEN** its class belongs to the required taxonomy set or explicitly maps to `unknown`

### Requirement: Failure Fingerprint
The system SHALL emit a normalized failure fingerprint composed of failure class, stage, stable signature, and key context fields for aggregation and trend analysis.

#### Scenario: Fingerprint supports grouping
- **WHEN** two runs fail with equivalent root cause
- **THEN** their failure fingerprint is identical except for run identifiers and timestamps
