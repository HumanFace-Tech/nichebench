# NicheBench Release Notes

> **Historical release notes.** Current pins are Python 3.13–3.14, LiteLLM
> `1.106.7`, and OpenCode `2.2.5`; see `pyproject.toml` and `AGENTS.md`.

## 0.3.0 — Leaner Scoring and Runtime V2

> **2026-11-10**

### What's New

- **LLM-judged quiz scoring** — `LiteLLMJudge` reads the candidate answer, selects a letter, and NicheBench compares it with the gold letter. The deterministic parser still runs on the same answer for audit (`selected`, `parse_reason`, `agrees`) but never decides the score. Judge calls retry up to three times; exhausted judge errors are counted separately and excluded from quiz averages.
- **Quiz v2 dataset** — The private data pack was repaired and rebalanced in place (`nichebench-data-drupal@1225a56`): 12 truncated and 12 mapping-typed choices fixed, gold distribution A=25 and B–F=24, `dataset_version: quiz-v2`.
- **Direct judge for code and bug fixing** — DeepEval was removed. Scores are recomputed from rubric criteria, clamped, and audited against the judge's own overall score. Judge failures are classified (`judge_unavailable`, `judge_invalid_json`, and related kinds), retried, and excluded from averages.
- **Structural sanity layer** — Code responses hard-fail only for an empty response or visible truncation; uncertain or unfenced content (prose, bare YAML, a single statement, an unfenced diff) is advisory and still reaches the judge, and lint and language checks are advisory.
- **Legacy `code_agent` removed** — The deprecated LangGraph prototype and its LangChain/LangGraph dependencies are gone. `drupal_runtime` is the only agentic path; legacy task runs fail with a clear message, and historical result files remain readable.
- **OpenCode V2 cage** — The runtime config targets OpenCode `2.2.5` with native V2 `providers`/`permissions`/`agents`. The `--pure` flag was removed because OpenCode 2.2.5 rejects it. The `nichebench/opencode-ddev:2.2.5` image must be built locally.
- **Lean dependencies** — Python is now `>=3.13,<3.15`; `litellm==1.106.7`; `pandas`, `pyarrow`, `deepeval`, and the LangChain family removed. Packaging no longer walks the private runtime data tree, so `poetry build` completes in under a minute.
- **Run integrity** — Judge thresholds honor the effective profile config. Judge outages, malformed judge output, invalid quiz data, and harness/environment failures are reported as `judge_*` or `config_error`, serialize null scores, and are excluded from model averages instead of silently lowering them. An explicit abstention (`selected: null` with a reason) remains a scored zero, not an excluded error. Runtime judge failures now use the same contract instead of becoming zero scores or catastrophic failures.
- **Prompt loading fixed** — The executor now resolves framework system prompts from the installed package path instead of the repository root, so quiz/code/bug prompts advertised by the framework are actually applied.
- **Unfenced code reaches the judge** — Structural sanity hard-fails only empty or truncated responses; unfenced or otherwise uncertain content (code, YAML, diffs, prose) is advisory instead of an automatic zero.

### Known Limits

- Runtime tasks require Docker and DDEV and are not exercised by CI. MiniMax judging was rate-limited (HTTP 429) during release verification; local-judge smokes were used for pipeline validation. The harness, cage, watchdog, and teardown were exercised against live DDEV, but Ornith stalled before producing a scored task, so a completed runtime check/judge smoke remains outstanding.

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
