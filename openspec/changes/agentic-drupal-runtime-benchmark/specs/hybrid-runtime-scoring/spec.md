## ADDED Requirements

### Requirement: Deterministic-first hybrid scoring
The system SHALL compute runtime scores using deterministic checks as primary signals and optional LLM-judge rubric as secondary signals.

#### Scenario: Deterministic checks gate pass status
- **WHEN** critical deterministic checks fail
- **THEN** overall task status SHALL NOT be `pass` regardless of judge score

#### Scenario: Weighted final score is produced
- **WHEN** deterministic and judge components are available
- **THEN** the runner SHALL compute final score using configured weights and include component breakdowns

#### Scenario: Deterministic-only mode is supported
- **WHEN** judge scoring is disabled or unavailable
- **THEN** the runner SHALL produce deterministic score outputs without failing the run pipeline
