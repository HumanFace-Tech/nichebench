## ADDED Requirements

### Requirement: Runtime SHALL bind agent to configured MUT model
The runtime framework SHALL launch the agent-under-test with the exact configured MUT provider/model for the run.

#### Scenario: MUT model binding is enforced at startup
- **WHEN** runtime starts agent execution
- **THEN** startup arguments include the configured MUT provider/model and do not fall back to host defaults [not just host-defaults, opencode comes with default free models that are pre-enabled / pre-installed - including web-search tools - we want those off, ideally having a control over those (i.e. if we enable web-fetch tool or not)]

#### Scenario: Effective model binding is auditable
- **WHEN** run metadata is generated
- **THEN** metadata records the effective MUT model binding used by the agent runtime

### Requirement: Runtime SHALL block ambient host capability inheritance
The runtime framework SHALL prevent ambient host OpenCode capabilities from altering benchmark behavior unless explicitly enabled by benchmark configuration.

#### Scenario: Host capability defaults are not implicitly enabled
- **WHEN** cage mode executes without explicit capability enables
- **THEN** host-provided defaults for external plugins/MCP/tooling do not become active in the run

#### Scenario: Explicit capability toggles are visible
- **WHEN** a run enables any non-default capability flag
- **THEN** metadata includes those toggles and their effective values

### Requirement: Runtime SHALL preserve unrestricted in-cage execution semantics
The runtime framework SHALL not impose per-command allowlists on the agent inside the cage, and SHALL rely on isolation boundaries and mount topology for containment.

#### Scenario: No command allowlist is required
- **WHEN** the agent issues shell/tool commands inside cage mode
- **THEN** runtime does not require commands to match an allowlist to execute

#### Scenario: Containment remains boundary-based
- **WHEN** unrestricted commands are executed in-cage
- **THEN** effects are limited to container runtime scope and configured mounts, and host escape paths are not implicitly granted
