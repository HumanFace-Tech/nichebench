# Copilot Instructions for NicheBench

This file contains evergreen guidance for all AI copilots interacting with the NicheBench project. It defines the project's purpose, repo structure, workflow standards, and operational patterns.

---

## 1. Project Overview

* **Name:** NicheBench
* **Goal:** Provide a LightEval‑powered CLI framework to benchmark AI models on framework‑specific tasks (e.g., Drupal 10/11 modules, debugging scenarios, quizzes).
* **Key Features:**

  * Quiz tasks, code‑generation tasks, bug‑fix tasks, each with custom metadata and checklist‑based scoring.
  * Auto-discovery framework system - add new frameworks by simply creating a tasks subdirectory.
  * High‑throughput parallel inference via `--num-procs`.
  * Dynamic framework detection and CLI generation.
  * Hardcoded sample data for rapid prototyping and development.

## 2. Repository Structure

**Microservice Architecture** - Clean Python project with src layout:

.
├── .github/
│   ├── copilot-instructions.md   # This file
│   └── workflows/
│       └── ci.yml                # GitHub Actions CI pipeline
├── .pre-commit-config.yaml       # Root-level Git hooks
├── .gitignore
├── LICENSE
├── README.md                     # Project documentation
├── setup.sh                     # Development environment setup
├── Makefile                      # Development commands
├── pyproject.toml               # Poetry project config
├── poetry.lock                  # Locked dependencies
├── DEVELOPMENT.md               # Development guide
├── src/                         # Python src layout (best practice)
│   └── nichebench/              # Actual Python package
│       ├── __init__.py
│       ├── __main__.py          # Entry point for python -m nichebench
│       ├── main.py              # CLI entry point with Typer
│       ├── tasks/               # Framework-specific task definitions (auto-discovered)
│       │   ├── __init__.py      # Auto-discovery framework loader
│       │   ├── data_loader.py   # Hardcoded sample data utilities
│       │   └── drupal/          # Example framework (auto-discovered)
│       │       ├── __init__.py
│       │       ├── tasks.py
│       │       ├── prompt_functions.py
│       │       └── system_prompts/
│       └── metrics/             # Custom evaluation metrics
│           ├── __init__.py
│           └── checklist.py     # Dynamic checklist evaluation
└── tests/                       # Test files

## 3. Workflow Standards

1. **Session Kick‑off:**
   * Read `README.md` to determine current priorities (TODO vs. DONE).
   * Sync on open issues & version milestones.

2. **Coding Style & Patterns:**
   * Follow PEP8 and project's `pyproject.toml` formatting rules.
   * Use `rich` for all CLI output (progress bars, tables).
   * Leverage LightEval abstractions (`Task`, `Metric`)—avoid reinventing the registry or parallelism.
   * Use *POETRY* to run commands (poetry run, etc.)

3. **Development Workflow:**
   * Use `./setup.sh` to set up the local development environment
   * Use `poetry shell` to activate the virtual environment
   * Use `make` commands for common development tasks
   * Service runs directly on host with Poetry virtual environment
   * Tests are co-located within the project directory
   * Poetry manages dependencies with proper caching

4. **Task Definitions:**
   * Every task must define a unique `TASK_NAME`, load its dataset via HF `load_dataset`, and implement `get_prompt(idx)`.
   * Metadata keys: `context`, `prompt`, `reference`, plus any custom fields (e.g., `checklist`, `docker_image`).

5. **Metric Implementations:**
   * Subclass LightEval's `Metric` interface.
   * Return a dict scalar per run (e.g., `{"checklist_success_rate": 0.75}`).

6. **Testing & CI:**
   * Unit‑test all new tasks and metrics in `tests/` (use `pytest`).
   * Ensure `nichebench --help` and `nichebench list-tasks` work locally.
   * Use pre-commit hooks for code quality (black, isort, flake8, mypy).
   * GitHub Actions CI runs tests on multiple Python versions.

## 4. Dataset Management

* **Storage:** Gated HF datasets (`nichebench/<task-name>`) containing zipped JSON/JSONL.
* **Versioning:** Tag each dataset release (e.g., `v1.0`, `v1.1`).
* **Access:** Use `load_dataset(..., token=True)` inside tasks.
* **Updates:** When tasks or schemas change, publish a new dataset version and update task code's dataset name if needed.

## 5. Versioning & Release

* **New Release Workflow:**

  1. Bump `project.version` in `pyproject.toml`.
  2. Tag the Git branch (e.g., `git tag v0.2.0`).
  3. Publish to PyPI or distribute via `pip install -e .`.
  4. **Update `README.md`:** Mark completed tasks, add changelog entry, set next version's TODOs.

* **Session Closure:**

  * Upon user confirmation that a session's deliverables are done - this requires some steps to take place:
  - **Update `.github/copilot-instructions.md` first - THIS MIGHT NOT BE NEEDED.**
  - **Update `README.md` second - THIS ALSO, might not be needed.**
  - **Before committing, run: git status, precommit (!important!), etc to make sure all is good and fixed**
  - **Only now, once everything is fixed - you can add / commit / push**


## 6. Dependencies & Integration

* **Core:** `lighteval>=0.9`, `questionary>=2.0`, transitive deps: `typer`, `rich`, `datasets`
* **Dev:** `pytest`, `black`, `isort`, `flake8`, `mypy`, `pre-commit`
* **Architecture:** Python 3.10+, Poetry dependency management, Docker containerization
* **Integration:** All tasks must register via LightEval's `--custom-task-modules` and `--custom-metric-modules` flags.

## 7. How This Differs / Relies on LightEval

* **Differences:** Focused exclusively on framework‑specific AI benchmarks (Drupal, WP, Svelte, etc.) with extensible checklists.
* **LightEval Reliance:** Uses LightEval's parallelism, model‑backend switching, caching, and result‑pushing capabilities.
* **Heavy Dependencies:** LightEval includes PyTorch/CUDA stack (~1.6GB) - necessary for ML model evaluation.
