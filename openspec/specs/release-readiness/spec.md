## Requirements

### Requirement: Release metadata and notes
The project SHALL identify the runtime-capable release as version `0.2.0` and SHALL provide
public release notes describing its material capabilities and known environment limitations.

#### Scenario: Package release inspection
- **WHEN** a contributor reads the package metadata and release notes
- **THEN** they can identify the release version, purpose, and required runtime environment

### Requirement: Reproducible public quality gates
The repository CI workflow SHALL install dependencies and run the same supported unit test, lint,
and type-check commands documented for local contributors from the repository root.

#### Scenario: Pull request verification
- **WHEN** CI runs for a pull request
- **THEN** it executes the documented Pytest, Ruff, and Mypy commands without relying on obsolete
  repository paths or undeclared quality tools

### Requirement: Private benchmark artifact boundary
The repository SHALL keep raw benchmark results, internal reports, credentials, and local provider
configuration out of tracked release artifacts while documenting the benchmark methodology and
private archival expectation.

#### Scenario: Release artifact review
- **WHEN** a maintainer reviews a release diff
- **THEN** it contains no raw results, private endpoints, credentials, or internal report content

### Requirement: Current operational documentation
Public documentation SHALL describe the current dependency constraints, supported quality gates,
runtime smoke procedure, and sequential DDEV runtime constraint.

#### Scenario: New operator setup
- **WHEN** an operator follows the public docs from a clean checkout
- **THEN** they can run local quality gates and identify the prerequisites for runtime smoke checks

### Requirement: Runtime smoke ownership boundary
The public runtime smoke procedure SHALL validate an existing, already-bootstrapped isolated DDEV
workspace and SHALL not claim to validate or repair private task-pack bootstrap behavior.

#### Scenario: Existing task workspace
- **WHEN** an operator provides an already-bootstrapped isolated task workspace
- **THEN** the smoke script reports the health of its five environment probes without modifying
  the task pack
