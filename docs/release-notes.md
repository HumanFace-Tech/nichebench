# NicheBench Release Notes

## 0.2.1 — Runtime Capture Update

> **2026-10-09**

### What's New

- **Opt-in runtime capture** — Durable, per-attempt event journals preserve observed MiniMax HTTP request/response bodies, OpenCode JSON events, process streams, and SQLite session snapshots outside disposable task workspaces.
- **Failure-path preservation** — Capture finalizes on normal exit, nonzero exit, timeout, watchdog termination, and interruption. Incomplete streams and quota/storage failures are marked rather than reported as complete.
- **Payload integrity and privacy controls** — Content-addressed payload blocks, checksums, private file permissions, credential-aware scrubbing, and a configurable per-task disk quota.
- **Pinned MiniMax M3/M3.1 metadata** — Model declarations are backported into the pinned OpenCode cage without upgrading OpenCode or changing prompts, permissions, provider SDK, or scoring.
- **Structured command observation** — Required-command checks now recognize completed Bash calls in OpenCode JSON logs as well as legacy command lines. Existing benchmark artifacts are not rewritten.
- **Runtime and tooling pins** — Runtime defaults match the OpenCode 1.18.3 cage image. The development environment declares mypy 1.18.2, and CI uses Poetry 2.4.1 for the lockfile.

### Known Limits

- Runtime capture is opt-in and currently supports the MiniMax-compatible HTTP runtime route only.
- It records provider-visible traffic and OpenCode/tool state, not provider-internal hidden reasoning or provider-side event ordering.
- Interrupted, withheld, or redacted payloads are explicitly marked incomplete or non-exact. Unknown credentials embedded in arbitrary content cannot be guaranteed absent.
- Full capture can use substantial disk space. The configured quota stops the run rather than silently truncating the archive.
- Required-command matching remains textual; command aliases and task-pack lexical checks are not semantically re-evaluated in this release.

---

## 0.2.0 — Runtime-Capable Release

> **2026-07-15**

### What's New

- **Agentic runtime tasks (`drupal_runtime`)** — Full task execution inside isolated Docker cages with DDEV bootstrap, deterministic checks, trajectory capture, and hybrid scoring. See [Runtime Documentation](./runtime/index.md).
- **Runtime cage isolation** — Input/output/ops island mount topology, git/bash wrapper scripts, and container lifecycle management for clean task isolation.
- **Runtime diagnostics and forensics** — `RuntimeTrace` post-hoc analysis, structured artifact inventory, and a [Diagnostics Playbook](./runtime/runtime-diagnostics-playbook.md) for failure triage.
- **Sequential DDEV constraint** — Runtime tasks require DDEV projects to run one at a time. Parallel runtime execution is not supported; attempting it will produce resource conflicts and incorrect results.
- **Artifact retention controls** — `runtime_artifact_retention` (`minimal | standard | full`) and `runtime_keep_workspaces` for managing disk usage during extended evaluation runs.

### Dependency Updates

- **LiteLLM** — Pinned to `>=1.89.1,<1.90`. The upper bound guards against breaking API changes that affect both MUT and judge call paths. Do not widen without testing.

### Known Limits

| Limitation | Detail |
|---|---|
| **Runtime requires Docker + DDEV** | Runtime smoke cannot run in environments without Docker or DDEV. CI does not execute runtime tasks. |
| **Sequential DDEV only** | Only one DDEV project can be active at a time per host. |
| **Provider credentials required** | MUT and judge calls require valid `GROQ_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY` (or equivalent for other LiteLLM providers). |
| **Private submodule data** | Runtime task manifests and classic task data live in private Git submodules (`nichebench-data-drupal`, `nichebench-drupal-runtime-pack`). |
| **Private benchmark artifacts** | Raw results, `/reports/`, `nichebench.yml`, credentials, and historical internal audits are not committed. See [benchmark artifact boundary](#benchmark-artifact-privacy). |

### Benchmark Artifact Privacy

Raw results, reports, local configs, credentials, and historical internal audits are **never committed** to this repository. Historical audits remain local-only and are excluded via `.gitignore`.

Public methodology docs describe the protocol, scoring semantics, and task structure — not the outcomes of any private benchmark run.

---

## 0.1.x — Classic Tasks Initial Release

Initial release covering `quiz`, `code_generation`, and `bug_fixing` classic task categories with LLM-as-a-Judge evaluation.
