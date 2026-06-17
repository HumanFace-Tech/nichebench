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
│   ├── providers/              # litellm_client, litellm_judge, langgraph agent
│   ├── metrics/                # DeepEval-compatible metric classes
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
│   ├── operations/             # Operations & maintenance
│   └── archive/reports/        # Dated audit snapshots
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
│  → judge → score   │  │  runtime/checks.py          │
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
LLM response (single or multi-turn conversation)
    │
    ▼
Judge evaluation (LLM-as-a-Judge with rubric checklist)
    │
    ▼
Score (Pass >66% / Partial 33-66% / Fail <33%)
```

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

### Score Types

| Score | Source | Formula |
|---|---|---|
| **Deterministic** | Checks defined in manifest | `passed_checks / total_checks` |
| **Judge** | LLM judge evaluating artifact bundle | Per rubric in manifest |
| **Hybrid** | Weighted blend | `deterministic_weight × deterministic + judge_weight × judge` |

Default weights: 50% deterministic / 50% judge. Weights are set per-manifest.

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
  runtime_container_ddev_image: nichebench/opencode-ddev:1.14.25
```

---

## Dependency Notes

`litellm` is pinned to `>=1.75.8,<1.82.6`. Do not widen this range without testing. The upper bound guards against breaking API changes that affect both MUT and judge call paths.

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
