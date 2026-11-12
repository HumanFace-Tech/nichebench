## 1. Project Overview

* **Name:** NicheBench
* **Goal:** Provide a flexible, extensible CLI framework to benchmark AI models on **framework-specific tasks** (Drupal first, others later).
* **Scope:** Drupal classic tasks (quiz, code generation, bug fixing) and `drupal_runtime` agentic tasks. `drupal_runtime` is the canonical path for new agentic tasks; the deprecated `code_agent` prototype was removed. Existing results and historical records remain unchanged.
* **Key Features:**

   * **Scoring:** Quiz uses an LLM judge that selects the letter from the candidate answer; a deterministic parser is recorded as an audit cross-check only. Explicit abstention is a scored zero. Code generation and bug fixing use LLM-as-a-Judge.
  * **Task categories:** Quiz, code generation, and bug fixing use classic runners. Agentic tasks use `drupal_runtime`, which runs against live DDEV.
  * **Criteria scoring**: Judge criteria map to true (1), partial (0.5), or false/missing (0); NicheBench recomputes the aggregate score.
  * **Runaway Protection**: Sophisticated repetition detection prevents infinite loops and excessive token consumption.
  * **Task Types:** quizzes (MCQ), code generation (multi-file, complex), bug fixing (patches).
  * **Dynamic Checklists:** each test case can provide its own evaluation criteria for the judge.
  * **Auto-Discovery:** new frameworks and tasks are discovered automatically from `frameworks/`.
  * **Rich CLI:** all output (progress bars, tables, summaries) uses the `rich` library with stacked result displays.
  * **Provider Agnostic:** powered by `litellm`, works with Groq, Together, Anthropic, OpenAI, etc.

---

## 2. Repository Structure

**src-based Python layout** managed with **Poetry**.

```
  nichebench/
  ├── README.md
  ├── docs/                   # architecture, runtime, task, and operations docs
  ├── nichebench.sample.yml   # sample configuration
  └── src/nichebench/
      ├── cli/                # CLI + Rich UI
      ├── core/               # discovery, datamodel, loaders, results
      ├── execution/          # orchestrator, runners, runtime execution
      ├── providers/          # LiteLLM client and judge adapter
      ├── metrics/            # quiz scoring and direct judge evaluation
      ├── frameworks/
      │   ├── drupal/         # classic tasks
      │   └── drupal_runtime/ # agentic runtime tasks
      ├── config/             # settings and profiles
      └── utils/              # helpers
```

* **Framework Packs:** Each framework (Drupal, WordPress, etc.) lives under `frameworks/<name>/` with its own `data/`, `prompts/`, and `registry.py`.
* **Data:** YAML files (`quiz/`, `code_generation/`, `bug_fixing/`) define test cases.
* **Prompts:** Python modules defining system prompts for both MUT (model under test) and Judge.

---

## 3. Workflow Standards

1. **Environment & Tooling:**

   * Always use **Poetry** (`poetry add`, `poetry run`). Never raw pip.
   * Also use poetry to run things (not python -m ...)
   * Python 3.13–3.14.
   * Dependencies: see `pyproject.toml`. DeepEval was removed — code/bug scoring calls the LiteLLM judge directly.

2. **Code Style:**

   * Follow PEP8 and project's `pyproject.toml` linting rules.
   * CLI always uses `rich` (tables, progress, panels).
   * Keep modules small and focused.

3. **Development Workflow:**

   * CLI entrypoint: `python -m nichebench`.
   * Add new CLI subcommands in `src/nichebench/cli/commands/`.
   * Test with `pytest -n auto`.

4. **Task Definitions:**

   * YAML schema includes `id`, `prompt`/`question`, `choices` (for quizzes), `correct_choice` or `checklist` for code/bug.
   * Each test case may include a **dynamic checklist** for Judge LLM.
   * System prompts for Judge live in `prompts/`, imported by registry. The executor resolves framework prompts from the installed package path under `src/nichebench/frameworks/` (or the installed package), not the repository root; framework MUT and judge prompts are applied.

5. **Metrics:**

   * **Quiz tasks:** `LiteLLMJudge.score_quiz` selects the letter; NicheBench compares it with the gold letter. The deterministic parser records `selected`, `parse_reason`, and `agrees` for audit only. Abstention (`selected: null`, `pass: false`, non-empty explanation) scores zero and is included in averages. Invalid task data is `config_error`, makes no judge call, and is excluded from averages. Judge calls retry up to 3 times; exhausted judge errors are excluded from averages.
   * **Code/Bug tasks:** direct `LiteLLMJudge` returns rubric criteria (no DeepEval). The score is recomputed from criteria and clamped. Judge calls retry up to 3 times; malformed non-dict criteria are retried as `judge_invalid_json`, not treated as a crash. Judge errors are excluded from averages. Empty/whitespace output and truncated fences fail structural sanity; uncertain or unfenced content is advisory and reaches the judge.
   * Criteria score true as 1.0, partial as 0.5, and false or missing as 0.0. Runtime recomputes its weighted score from manifest criteria, scoring each criterion at most once. Judge failures and runtime setup/image/Docker/network/CLI/config failures are excluded from averages as `judge_errors` or `config_errors`; reports show `ERROR`/`N/A`, not 0% or a deterministic-only hybrid fallback.

6. **Results:**

   * Every run writes into `results/<framework>/<task>/<model>/<timestamp>/`.
   * `details.jsonl`: raw per-test results.
   * `summary.json`: aggregated scores.
   * CLI `report` command renders rich tables.

7. **Parallelism:**

   * Use Python `multiprocessing` for parallel jobs.
   * Provide progress updates with `rich.progress`.

8. **Conversation behavior:**

   * Code generation runs in one turn.
   * Bug fixing uses a single-shot fix plus a finalization nudge; `max_turns` comes from its manifest and is clamped to 1–8.

---

## 4. Dataset Management

* **Local Data First:** YAML tasks under `frameworks/<name>/data/`.
* **HF Integration Later:** export to Hugging Face dataset when stable (`nichebench/<framework>_<task>`).
* **Versioning:** `v1.0`, `v1.1` tags per dataset update.

---

## 5. Versioning & Release

* Versions managed in `pyproject.toml`.
* Release workflow:

  1. Bump version
  2. Tag branch (`git tag v0.1.0`)
  3. Publish to PyPI (optional)
  4. Update README changelog

---

## 6. How This Differs From Other Eval Frameworks

* **Not LightEval:** we don't rely on its registry/parallelism.
* **Not HuggingFace Eval Harness:** we don't care about generic benchmarks (MMLU, HellaSwag, etc).
* **Niche Only:** NicheBench is about framework-specific, checklist-driven tasks (Drupal, WP, Svelte, etc).
* **Scoring:** Quiz uses an LLM judge (selected letter vs gold); code/bug use judge-based rubric scoring. Runtime combines deterministic checks with an optional judge.

---

## 7. Copilot Rules of Thumb

* Use the LLM judge for quiz, code, and bug fixing; keep the quiz parser as an audit cross-check, not the score.
* Never hardcode provider APIs; always go through `litellm_client`.
* Keep the **CLI** beautiful and clear (progress, results, summaries with `rich`).
* Framework packs must be **plug-and-play** (just drop in `frameworks/foo/`).
* Keep metrics modular. Quiz, code, and bug-fixing scoring all use the direct judge; quiz keeps a parser for audit only.
* Don't add unrelated frameworks until requested.
