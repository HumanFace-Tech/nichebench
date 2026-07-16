## Requirements

### Requirement: Score-like judge output normalization
Classic task summary aggregation SHALL accept finite numeric values and finite numeric strings from
judge output as scores, and SHALL convert them to floats before categorization or averaging.

#### Scenario: Numeric string judge score
- **WHEN** a classic task judge returns `"0.95"` as `overall_score`
- **THEN** the result is categorized and included in averages as score `0.95` without error

### Requirement: Invalid score safety
Classic task summary aggregation SHALL treat booleans, missing values, non-numeric values, and
non-finite numeric values as its documented safe fallback rather than raising or serializing an
invalid aggregate.

#### Scenario: Non-finite judge score
- **WHEN** a judge returns `"nan"` or an infinite numeric score
- **THEN** the result uses the safe fallback score and summary generation completes
