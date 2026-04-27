## ADDED Requirements

### Requirement: Runtime task manifest contract
The system SHALL validate runtime manifests against a schema that defines source, environment, agent budget/profile, checks, scoring weights, and required deliverables.

#### Scenario: Manifest validation succeeds for required fields
- **WHEN** a runtime task includes required schema fields
- **THEN** the system SHALL accept the manifest for execution

#### Scenario: Manifest validation fails for missing runtime fields
- **WHEN** required runtime fields are missing or malformed
- **THEN** the system SHALL fail fast with explicit validation errors

#### Scenario: Setup mode is explicit per task
- **WHEN** a runtime task is configured
- **THEN** the manifest SHALL declare setup mode (`config_import` or `db_snapshot`) and required setup inputs
