## Context

NicheBench runtime tasks currently default to host-mode execution for OpenCode, which means the agent process inherits host process privileges even when workspace paths and temp storage are isolated. This weakens containment guarantees and allows ambient host configuration/capability bleed-through (plugins, MCP, sessions, defaults). The runtime harness already manages DDEV lifecycle and deterministic checks, so we can separate control-plane orchestration from the agent execution plane without losing benchmark utility.

Stakeholders are benchmark maintainers (safety/reproducibility), task authors (clear execution contract), and model evaluators (fair, comparable scoring). Primary constraints: keep runtime tasks functional, preserve rich artifact capture, and avoid destructive privilege escalation paths.

## Goals / Non-Goals

**Goals:**
- Execute OpenCode runtime tasks in a pinned, isolated container by default.
- Remove dependence on host OpenCode state/config and ambient capabilities.
- Preserve unrestricted in-agent behavior inside the cage while exposing only explicit artifact islands back to the harness.
- Guarantee full trace capture (run log, trajectory, checks, metadata) through deterministic bridge paths.
- Keep a clear execution contract for model binding and capability toggles.

**Non-Goals:**
- Redesigning task manifests or deterministic scoring semantics.
- Creating per-command allowlists for agent shell actions.
- Reworking DDEV internals beyond what is required to support isolated agent execution.
- Supporting unpinned OpenCode images in canonical benchmark mode.

## Decisions

### 1) Canonical runtime execution is containerized cage mode
**Decision:** Make containerized OpenCode execution the canonical runtime path for benchmark runs, with host mode retained only as an explicit compatibility fallback.

**Rationale:** Container isolation provides a stable boundary between harness host and agent-under-test while keeping behavior reproducible across environments.

**Alternatives considered:**
- Keep host mode with stronger prompts/flags: insufficient as a hard isolation boundary.
- Run all orchestration inside one monolithic container: increases operational complexity and weakens inspectability.

### 2) Pinned OpenCode image + benchmark-owned startup contract
**Decision:** Require a pinned OpenCode container image (digest or immutable tag) and benchmark-owned startup arguments (forced MUT model, explicit mode/profile flags, isolated HOME/XDG paths).

**Rationale:** Eliminates drift from local host installs and prevents inherited defaults from changing run behavior.

**Alternatives considered:**
- Floating `latest` image: non-reproducible.
- Host binary with version checks: cannot guarantee plugin/config isolation.

### 3) No command allowlist, but strict boundary via artifact islands
**Decision:** Do not impose command allowlists inside the agent cage. Instead, enforce boundary by mount topology:
- input island (task instructions and repo workspace)
- output/trace island (artifacts, logs, trajectories)
- optional ops island for harness-mediated diagnostics

**Rationale:** Preserves unrestricted agent behavior while keeping exfiltration and host traversal constrained by container boundary and mounted surfaces.

**Alternatives considered:**
- RPC allowlist tool: safer but changes benchmark semantics and agent behavior.
- Full host passthrough: undermines isolation goals.

### 4) Control plane / agent plane split
**Decision:** Keep environment lifecycle (workspace setup, DDEV start/stop, deterministic checks, scoring) in harness control plane; let agent plane focus on task implementation and optional in-cage verification commands.

**Rationale:** Maintains deterministic benchmarking and reduces privilege granted to agent process.

**Alternatives considered:**
- Let agent own environment lifecycle: non-deterministic and harder to audit.

### 5) Full trajectory persistence is mandatory artifact output
**Decision:** Persist trajectory artifacts (including system prompt and available reasoning/tool events) through the trace island as a first-class requirement for runtime runs.

**Rationale:** Debuggability and auditability require complete execution evidence per trial.

**Alternatives considered:**
- Best-effort traces only: insufficient for failure analysis.

## Risks / Trade-offs

- **[Risk] DDEV-in-devcontainer-in-container depth causes operational brittleness** → **Mitigation:** define a supported container topology, add preflight checks, and document required mounts/runtime flags.
- **[Risk] Strict isolation can break legacy tasks relying on host assumptions** → **Mitigation:** keep explicit host fallback mode and migration guidance; gate canonical scoring to cage mode.
- **[Risk] Pinned image updates become maintenance overhead** → **Mitigation:** define update cadence and compatibility smoke tests.
- **[Risk] Reasoning visibility varies by model/provider** → **Mitigation:** store all available raw trace parts and annotate absent reasoning as provider-limited, not harness failure.
- **[Risk] Larger artifact payloads increase storage cost** → **Mitigation:** keep retention tiers while making trajectory inclusion mandatory for benchmark-grade runs.

## Migration Plan

1. Introduce cage-mode config defaults and image pin fields.
2. Implement container launch contract and island mounts in runtime executor.
3. Wire artifact collection exclusively through output/trace islands.
4. Run compatibility matrix on existing runtime tasks (001–005).
5. Mark host mode as compatibility-only in docs and CLI output.
6. Roll out as default for benchmark profiles; retain rollback toggle to host mode.

Rollback strategy: switch runtime mode back to host in evaluation config while preserving artifact schema and scoring behavior.

## Open Questions

- Q: Should canonical cage mode allow Docker socket passthrough for in-agent `ddev` commands, or require harness-mediated environment operations only?
- A: We want the agent to be able to run ddev commands, that's a must, like a dev would -> ideally using docker-in-docker.

--

- Q: Which container hardening defaults are mandatory (`--read-only`, dropped caps, seccomp profile) versus optional for compatibility?
- A: I don't know -> as long as we don't share the customized opencode (that you're using on host, which is MY creation, with custom MCP tools and sub-agents) - and we don't spill secrets (test-expectations) - then we're good.

--


- Q: Should benchmark publication reject runs that are not in cage mode by default?
- A: Wtf is benchmark publication? We should just default to caged-runs (it also makes it more stable as we don't depend on hosts' opencode and whether it's broken or not).
