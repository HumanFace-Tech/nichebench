## ADDED Requirements

### Requirement: Runtime task execution in Drupal workspace
The system SHALL execute `task_type: runtime` tasks inside isolated Drupal workspaces where the agent can run required developer commands and produce code/config changes.

#### Scenario: Runtime task starts with isolated workspace
- **WHEN** a runtime task is started
- **THEN** the runner SHALL create a unique workspace and initialize runtime services for that task instance

#### Scenario: Agent executes task commands
- **WHEN** the task is active
- **THEN** the runner SHALL allow agent command execution according to the configured tool profile and capture command outputs

#### Scenario: Runtime task produces artifacts
- **WHEN** the task run completes or times out
- **THEN** the runner SHALL persist task artifacts including git diff/patch, command logs, and check results
