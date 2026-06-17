## Why

Runtime benchmarking currently uses `runtime_mode: host` by default, which executes OpenCode as a host process with user-level filesystem and shell reach. This makes benchmark safety and reproducibility weaker than intended for agent evaluations that must be isolated, deterministic, and auditable.

## What Changes

- Add a hardened runtime execution mode where OpenCode runs in a pinned, isolated container by default for runtime tasks.
- Enforce benchmark-controlled agent startup: single MUT model binding, no inherited host OpenCode session/config state, and no ambient host MCP/plugin surface.
- Introduce explicit artifact bridge mounts (“islands”) so the harness can collect full trajectories and runtime evidence without granting broad host access.
- Define strict separation between control plane (harness orchestrates environment/checks) and agent plane (agent performs task work inside cage).
- Add reproducibility and security guardrails around container image versioning, runtime flags, and capability toggles.

## Capabilities

### New Capabilities
- `runtime-agent-cage`: Execute runtime agent runs inside an isolated, pinned OpenCode container with benchmark-owned startup and config.
- `runtime-artifact-islands`: Provide dedicated mount islands for inputs/outputs/trace collection so harness can inspect all run evidence post-execution.
- `runtime-model-and-capability-lock`: Bind the MUT model at runtime and prevent unintended inherited host capabilities/state from affecting benchmark behavior.

### Modified Capabilities
- None.

## Impact

- Affected systems: runtime executor orchestration, container launch path, profile/capability resolution, runtime metadata and artifact capture.
- Potential config/API impact: evaluation runtime options (mode defaults, container hardening knobs, image pinning, trajectory controls).
- Operational impact: benchmark runs shift from host-process execution to containerized execution for stronger isolation and repeatability.
