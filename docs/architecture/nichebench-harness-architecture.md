# NicheBench Harness Architecture

## Repository Shelves

```
nichebench/
├── nichebench.yml              # Local config (gitignored)
├── nichebench.sample.yml       # Config template
├── src/nichebench/
│   ├── cli/                    # CLI commands + Rich UI
│   │   ├── commands/           # run, list, report, show, forensics
│   │   └── rich_views/         # Tables, run views, report views
│   ├── config/                 # nichebench_config.py, profiles, settings
│   ├── core/                   # datamodel, discovery, profiles, loader_yaml
│   ├── execution/              # Orchestrator, runners, result, persistence
│   │   ├── runtime/            # Runtime executor, workspace, DDEV, checks
│   │   │   ├── cage/           #   Docker container lifecycle, retry, watchdog
│   │   │   ├── executor/       #   stage flow + catastrophic failure shortcut
│   │   │   ├── scoring/        #   check runner, validation, deterministic ops
│   │   │   ├── trajectory/     #   session_files, sqlite, polling, debug_dump
│   │   │   ├── artifacts/      #   persistence, redaction, tool policy
│   │   │   ├── workspace/      #   Workspace model, ddev, cleanup
│   │   │   └── wrappers/       #   cage git/bash wrapper scripts
│   │   ├── diagnostics/        # RuntimeTrace + forensics (post-hoc analysis)
│   │   └── runners/            # mut.py (MUT runner), judge.py (judge runner)
│   ├── providers/              # LiteLLM client and judge adapter
│   ├── metrics/                # Quiz parser for audit + direct LiteLLM judge for quiz/code/bug; no DeepEval
│   ├── frameworks/
│   │   ├── drupal/             # Classic Drupal tasks (quiz, code_gen, bug_fixing)
│   │   │   ├── data/           # → submodule: nichebench-data-drupal (private)
│   │   │   └── prompts/        # MUT + judge prompts
│   │   └── drupal_runtime/     # Runtime Drupal tasks (drupal_runtime)
│   │       ├── data/           # → submodule: nichebench-drupal-runtime-pack (private)
│   │       └── prompts/        # Runtime judge prompts
│   └── utils/                  # git.py, io.py
├── docs/                       # This documentation shelf
│   ├── architecture/           # This file
│   ├── runtime/                # Runtime task documentation
│   ├── tasks/                  # Classic task documentation
│   └── operations/             # Operations & maintenance
├── scripts/                    # runtime_smoke.py, runtime_maintenance.py, forensics.py
├── tests/unit/                 # Unit tests
└── openspec/                   # Design proposals and task specs
```

> **Note:** `core/executor.py` and `core/workspace.py` (legacy shim locations) were removed. Runtime code lives under `execution/runtime/`.

---

## Execution Layers

```
┌─────────────────────────────────────────────────────┐
│                    CLI (cli/)                       │
│         run | list | report | show | forensics      │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              Orchestrator (execution/)              │
│   Task discovery → dispatch → parallel execution    │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │                         │
┌─────────▼──────────┐  ┌──────────▼──────────────────┐
│  Classic Runner    │  │     Runtime Runner          │
│  (quiz/code_gen/   │  │  (drupal_runtime)           │
│   bug_fixing)      │  │                             │
│                    │  │  execution/runtime/         │
│  MUT → response    │  │  workspace/ddev.py          │
│  quiz: judge       │  │  runtime/checks.py          │
│  code/bug: judge   │  │                             │
│                    │  │  runtime/executor/          │
└────────────────────┘  └─────────────────────────────┘
```

---

## Classic Task Flow

```
manifest YAML
    │
    ▼
MUT prompt (framework prompt + context + question)
    │
    ▼
LLM response (single-turn code generation; bug-fix response plus finalization nudge)
    │
    ▼
Quiz: LiteLLMJudge selects a letter; parser records an audit cross-check. Code/bug: direct LiteLLMJudge rubric call.
    │
    ▼
Quiz: judge-selected letter compared with gold for binary pass/fail; code/bug: criteria recompute → clamped score
```

Quiz, code, and bug judge requests retry up to 3 times after transient or parse failures. Quiz compares the judge-selected letter with gold; its deterministic parser output (`selected`, `parse_reason`, `agrees`) is audit-only. Quiz results record `judge_method="llm"` and the configured judge model; `judge_score` and `final_score` are binary and `deterministic_score` is null. Exhausted quiz judge failures use `judge_unavailable`, `judge_invalid_json`, `judge_missing_selected`, or `judge_out_of_range`, count as `judge_errors`, and are excluded from quiz averages. Code/bug judge failures use `judge_unavailable` or `judge_invalid_json` and are also excluded from averages. Harness/configuration failures use `error_kind=config_error`; they are reported as `config_errors`, separately from `judge_errors`. Runtime judge outages use the same `judge_*` exclusion, and runtime setup/image/Docker/network failures are classified `config_error` instead of becoming zero model scores. `judge_overall_score` is retained for audit only; code/bug `judge_score` and `final_score` use the recomputed criteria score.

---

## Runtime Task Flow (Cage Mode)

```
manifest YAML + task branch checkout
    │
    ▼
isolated workspace  ──────────────────────────────────┐
    │                                                 │
    ▼                                                 ▼
DDEV bootstrap (start + drush cim)            TASK.md injection
    │                                                 │
    ▼                                                 ▼
OpenCode cage MUT ── Docker ── mounts ──► workspace  │
  │  (islands: input / output-trace / ops)           │
  │                                                 │
  ▼                                                 ▼
deterministic checks              run.log / trajectory
    │                                                 │
    ▼                                                 ▼
checks.json + focused validation  final.diff + artifacts
    │
    ▼
optional LLM judge ──► hybrid score
```

### Cage Island Topology

| Island | Host path | Container path | Purpose |
|---|---|---|---|
| input | `/nichebench/islands/input` | `/nichebench/islands/input` | Read-only task inputs |
| output-trace | `/nichebench/islands/output-trace` | `/nichebench/islands/output-trace` | Run artifacts + trace |
| trace subpath | — | `/nichebench/islands/output-trace/trace` | Trajectory files |
| ops (optional) | `/nichebench/islands/ops` | `/nichebench/islands/ops` | Operational scripts |

`metadata.json` records `island_topology` for auditability.

---

## Scoring Internals

See [Runtime Reporting & Scoring](../runtime/runtime-reporting-scoring.md) for full detail.

### Runtime score types

| Score | Source | Formula |
|---|---|---|
| **Deterministic** | Checks defined in manifest | `passed_checks / total_checks` |
| **Judge** | LLM judge evaluating artifact bundle | Per rubric in manifest |
| **Hybrid** | Weighted blend | `deterministic_weight × deterministic + judge_weight × judge` |

Runtime default weights are 70% deterministic and 30% judge. Weights are set per manifest. Classic code-generation and bug-fixing tasks do not use this hybrid score.

### Critical Gate

Any critical check failure → `passed=False` regardless of score.

Threshold default: `0.7`

---

## Configuration Schema

```yaml
mut:
  provider: "groq"           # openai | anthropic | groq | llamacpp | ...
  model: "gemma2-9b-it"
  parameters:
    temperature: 0.0
    max_tokens: 4096

judge:
  provider: "openai"
  model: "gpt-4o"

evaluation:
  parallelism: 1
  enable_runtime_tasks: false  # Runtime tasks disabled by default

profiles:
  fast:
    mut: {provider: "groq", model: "llama-3.1-8b-instant"}
    judge: {provider: "groq", model: "llama-3.1-70b-versatile"}
```

Precedence: **CLI args > env vars > profile > defaults**

### Runtime-Specific Config

```yaml
evaluation:
  runtime_mode: cage            # cage (default) | host (compat)
  runtime_container_image: ...   # custom cage image
  runtime_timeout_minutes: 90
  runtime_artifact_retention: standard  # minimal | standard | full
  runtime_keep_workspaces: false
  runtime_container_enable_ddev: true
  runtime_container_ddev_auto_build: true
  runtime_container_ddev_image: nichebench/opencode-ddev:2.2.5
```

---

## Dependency Notes

The planned `litellm` security pin is `==1.106.7`. Do not change the target without testing the MUT and judge call paths.

The runtime cage uses the native OpenCode V2 `providers`/`permissions`/`agents` configuration with image `ghcr.io/anomalyco/opencode:2.2.5`. The derived `nichebench/opencode-ddev:2.2.5` image must be rebuilt from that base image; do not assume it is already built.

---

## Provider Support

NicheBench uses `litellm` for provider abstraction. Any provider supported by litellm (OpenAI, Anthropic, Groq, Ollama, llama.cpp, and many more) works without code changes.

Environment variables for API keys:
```
GROQ_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
NICHEBENCH_JUDGE=openai/gpt-4o   # optional judge override
```

---

## Benchmark Artifact Privacy

The following are intentionally **never committed** to the repository (see `.gitignore`):

| What | Why |
|---|---|
| `results/`, `/reports/` | Raw benchmark outputs and generated reports |
| `nichebench.yml` | Local operator configuration with credentials and private endpoints |
| `.env`, `.env.local` | Provider API keys and secrets |
| `workspaces/` | Ephemeral runtime task workspaces |
| Historical internal audits | Local-only historical material; never part of public releases |

Public documentation describes the **methodology** — scoring semantics, task types, and evaluation protocols — not private benchmark outcomes. Raw results, reports, local configs, credentials, and historical internal audits are not committed; historical audits remain local-only.
