# NicheBench Release Notes

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
