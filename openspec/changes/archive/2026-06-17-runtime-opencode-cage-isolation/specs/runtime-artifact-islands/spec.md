## ADDED Requirements

### Requirement: Runtime SHALL expose explicit artifact islands
The runtime framework SHALL define explicit mount islands for agent execution so that harness-controlled inputs and outputs are separated and auditable.

#### Scenario: Input and output islands are distinct
- **WHEN** a cage mode runtime run is created
- **THEN** task/repository inputs and artifact outputs are mounted through distinct island paths with documented purpose

#### Scenario: Island topology is recorded
- **WHEN** a cage mode runtime run starts
- **THEN** run metadata includes island path mappings used for the run

### Requirement: Full per-trial evidence SHALL be persisted through output islands
The runtime framework SHALL persist full runtime evidence per trial through the output island, including run log, final diff, checks, metadata, and trajectory artifacts.

#### Scenario: Trial artifacts are not overwritten
- **WHEN** a runtime task executes with multiple trials
- **THEN** each trial writes to an independent artifact location and prior trials remain available for debugging

#### Scenario: Required evidence files exist per trial
- **WHEN** a trial completes
- **THEN** artifact output includes at least metadata, checks, run log, final diff, and trajectory payloads for that trial

### Requirement: Artifact bridge SHALL support post-run forensic analysis
The runtime framework SHALL preserve enough structured trace data to reconstruct tool calls and model behavior after run completion.

#### Scenario: Trajectory contains structured events
- **WHEN** trajectory capture succeeds
- **THEN** trajectory output includes message sequence and structured event details needed to inspect tool usage and run progression

#### Scenario: Missing provider reasoning is explicit
- **WHEN** provider/model does not expose reasoning blocks
- **THEN** trajectory remains valid and indicates absent reasoning without failing artifact generation
