## 1. Runtime mode and configuration contract

- [x] 1.1 Change runtime defaults so canonical runtime execution uses cage/container mode, with host mode as explicit compatibility override.
- [x] 1.2 Add/validate pinned OpenCode image configuration fields and fail fast when cage mode lacks a pinned image reference.
- [x] 1.3 Extend runtime metadata to record effective runtime mode, exact image reference, and model/capability lock fields.

## 2. Cage launch hardening and startup binding

- [x] 2.1 Refactor runtime container launch path to enforce benchmark-owned startup contract (forced MUT model binding and isolated runtime state paths).
- [x] 2.2 Ensure host OpenCode state/config are not inherited by cage runs (run-scoped HOME/XDG and explicit config surface).
- [x] 2.3 Keep unrestricted in-cage command semantics while preserving containment through container boundary and mount topology.

## 3. Artifact islands and evidence bridge

- [x] 3.1 Implement explicit island mount layout (input island, output/trace island, optional ops island) for cage runs. Normally we will input the branch we're testing (minus the actual answers and stuff) - and collect artifacts as output.
- [x] 3.2 Route runtime evidence writes through output/trace island and preserve per-trial artifact separation.
- [x] 3.3 Record island topology in runtime metadata for auditability. Also update AGENTS.md / README.md about these.

## 4. Full trajectory and forensic trace guarantees

- [x] 4.1 Guarantee trajectory persistence per trial with system prompt and structured event parts when available.
- [x] 4.2 Ensure trajectory remains valid when provider reasoning is unavailable, with explicit absence semantics.
- [x] 4.3 Add tests that verify required per-trial evidence files exist and are non-overwriting across multi-trial runs.

## 5. Compatibility, validation, and rollout

- [x] 5.1 Add configuration and preflight tests covering cage defaults, host compatibility override, and invalid pin scenarios.
- [x] 5.2 Run runtime compatibility matrix across existing runtime tasks and capture regressions/fixes.
- [x] 5.3 Update docs/CLI messaging to mark host mode as compatibility-only and document rollback toggle.
