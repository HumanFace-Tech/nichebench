## ADDED Requirements

### Requirement: Tool access profile enforcement
The system SHALL enforce explicit tool-access profiles for runtime tasks and record the active profile in run metadata.

#### Scenario: Offline profile restricts network-capable tools
- **WHEN** `offline_cli` profile is selected
- **THEN** web search and browser automation tools SHALL be disabled

#### Scenario: Web profile enables search without browser
- **WHEN** `web_cli` profile is selected
- **THEN** web search SHALL be enabled and browser automation SHALL remain disabled unless explicitly allowed

#### Scenario: Profile and flags are auditable
- **WHEN** a runtime run completes
- **THEN** the result metadata SHALL include effective profile and resolved tool flags (`allow_web_search`, `allow_browser`, `allow_mcp`, `allow_external_network_for_shell`)
