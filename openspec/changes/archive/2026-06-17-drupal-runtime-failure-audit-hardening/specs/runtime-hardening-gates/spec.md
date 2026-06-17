## ADDED Requirements

### Requirement: Runtime Hardening Quality Gates
Runtime pipeline changes SHALL pass hardening quality gates that validate trace completeness, failure classification determinism, artifact schema integrity, and cleanup completion.

#### Scenario: Gate fails on missing diagnostics invariants
- **WHEN** a runtime pipeline change omits required diagnostics invariants
- **THEN** the hardening gate fails before merge

### Requirement: Compatibility Drift Detection
The runtime validation suite SHALL include checks for compatibility drift across OpenCode version, provider protocol behavior, runtime container image, and DDEV execution assumptions.

#### Scenario: Drift is detected before release
- **WHEN** an upgrade changes protocol or runtime behavior
- **THEN** compatibility checks flag the drift with actionable diagnostics

### Requirement: Regression Safety for Failure Reporting
The runtime suite SHALL verify that known failure fixtures continue to map to expected failure classes and fingerprints.

#### Scenario: Classification regression is blocked
- **WHEN** a code change alters failure mapping for known fixtures
- **THEN** regression checks fail and report the mismatch
