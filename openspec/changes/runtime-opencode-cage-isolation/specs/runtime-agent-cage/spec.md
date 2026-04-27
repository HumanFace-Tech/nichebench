## ADDED Requirements

### Requirement: Runtime agent SHALL execute in isolated cage mode by default
The runtime framework SHALL execute the agent-under-test in containerized cage mode by default for runtime tasks, with host mode available only as an explicit compatibility override.

#### Scenario: Default runtime uses cage mode
- **WHEN** a runtime task is executed without an explicit runtime mode override
- **THEN** the harness starts the agent in containerized cage mode

#### Scenario: Compatibility override to host mode is explicit
- **WHEN** a runtime task configuration requests host mode explicitly
- **THEN** the harness executes in host mode and records the non-cage execution mode in run metadata

### Requirement: Cage mode SHALL use benchmark-pinned OpenCode runtime
The runtime framework SHALL start the agent with a pinned OpenCode image reference controlled by benchmark configuration.

#### Scenario: Pinned image is required
- **WHEN** cage mode is selected and no pinned image reference is configured
- **THEN** execution fails fast with a configuration validation error

#### Scenario: Pinned image reference is recorded
- **WHEN** cage mode run starts successfully
- **THEN** run metadata includes the exact image reference used for agent execution

### Requirement: Cage mode SHALL isolate agent state from host defaults
The runtime framework SHALL isolate agent runtime state/config from host OpenCode defaults by using run-scoped state paths and benchmark-owned startup configuration.

#### Scenario: Host OpenCode state is not reused
- **WHEN** two runs execute with different run identifiers
- **THEN** each run uses independent agent state paths and does not continue the other run's session state

#### Scenario: Benchmark startup contract is enforced
- **WHEN** cage mode starts the agent
- **THEN** startup arguments enforce benchmark-selected model binding and benchmark-owned config inputs
