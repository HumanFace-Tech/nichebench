## ADDED Requirements

### Requirement: Branch-based baseline resolution
The system SHALL support task baseline authoring via branches and SHALL resolve each run to an immutable commit SHA before execution.

#### Scenario: Baseline branch is resolved at run start
- **WHEN** a task manifest specifies `source.base_branch`
- **THEN** the runner SHALL resolve that branch to a commit SHA and record it as `resolved_sha`

#### Scenario: Run metadata stores provenance
- **WHEN** a runtime run is recorded
- **THEN** run metadata SHALL include `base_branch` and `resolved_sha`

#### Scenario: Official run enforces frozen provenance
- **WHEN** a run is marked as official or leaderboard-bound
- **THEN** the system SHALL evaluate using frozen commit provenance and SHALL reject missing `resolved_sha`
