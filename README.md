# NicheBench

> LightEval-powered CLI framework for benchmarking AI models on framework-specific tasks

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

## Overview

NicheBench provides a comprehensive benchmarking framework for evaluating AI models on framework-specific tasks such as:

- **Drupal 10/11** module development
- **Code debugging** scenarios
- **Technical quizzes** and knowledge assessments

Built on top of [LightEval](https://github.com/huggingface/lighteval), NicheBench leverages high-throughput parallel inference, hardcoded sample data for development, and checklist-based scoring systems.

## Key Features

- 🚀 **High-throughput parallel inference** via `--num-procs`
- 📊 **Checklist-based evaluation** with custom scoring metrics
- 🔄 **Auto-discovery framework system** - add new frameworks by simply creating task subdirectories
- 🐳 **Docker support** for consistent environments
- 🔧 **Extensible task system** for adding new frameworks
- 📈 **Rich CLI output** with progress bars and tables
- 💾 **Hardcoded sample data** for rapid prototyping and development

## Quick Start

### Prerequisites

- Python 3.10+
- Poetry (optional - will be installed automatically)

## Installation & Setup

### Development Setup (Recommended)

For development with IDE support and shared dependencies:

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd nichebench
   ```

2. **Setup development environment:**

   ```bash
   cd nichebench
   ./setup.sh
   ```

3. **Activate the environment:**

   ```bash
   poetry shell
   ```

4. **Test the CLI:**

   ```bash
   nichebench --help
   nichebench list-tasks
   ```

5. **Configure your IDE:**
   - **VS Code:** Set Python interpreter to `./.venv/bin/python`
   - **PyCharm:** Point Project SDK to `./.venv/bin/python`
   - **Other IDEs:** Use `./.venv/bin/python` as interpreter

The development setup creates a virtual environment in `./.venv/` that your IDE can access for proper IntelliSense and debugging.

### Alternative: Using Make Commands

```bash
# Setup and enter shell
make setup
make dev-shell

# Or run commands directly
make run-help
make test
make format
```

### Basic Usage

```bash
# List available tasks
nichebench list-tasks
docker compose -f docker-compose.dev.yml run --rm nichebench-dev poetry run nichebench run drupal_module_quiz --model "gpt-3.5-turbo"

# Production commands
docker compose run --rm nichebench nichebench list-tasks
docker compose run --rm nichebench nichebench run drupal_module_quiz --model "gpt-3.5-turbo"
```

## Docker Architecture

NicheBench uses a **"one-venv-for-everything"** pattern that eliminates dependency duplication:

### Development vs Production

| Aspect | Development (`docker-compose.dev.yml`) | Production (`docker-compose.yml`) |
|--------|----------------------------------------|-----------------------------------|
| **Base Image** | `python:3.10-slim` (no build) | Custom built image with Dockerfile |
| **Dependencies** | Auto-installed to shared `./.venv/` | Pre-installed in image |
| **Code Mount** | Live bind-mount for hot reload | Copied into image at build time |
| **Venv Location** | Host `./.venv/` (shared with IDE) | Inside container (isolated) |
| **Startup Time** | Fast after first run | Instant (pre-built) |
| **Use Case** | Development, debugging, IDE integration | Production deployment |

### Key Benefits

- **No duplication:** Only one 15GB virtual environment on disk
- **IDE integration:** Your editor sees the exact same dependencies Docker uses
- **Fast iteration:** Changes to code are immediately visible in container
- **Production ready:** Frozen, reproducible builds for deployment

### Files Overview

```text
.
├── docker-compose.yml          # Production (frozen image)
├── docker-compose.dev.yml      # Development (shared .venv)
├── .venv/                      # Shared virtual environment (git-ignored)
└── nichebench/
    ├── Dockerfile              # Production image definition
    ├── pyproject.toml          # Poetry dependencies
    └── src/...                 # Source code
```

The trick: **Never layer a bind-mount on top of something already in the image.** Instead, let the container populate an empty host directory so both sides see the same files.

## Project Structure

**Microservice Architecture:**

```text
.
├── .github/                    # GitHub workflows and documentation
├── docker-compose.yml          # Production orchestration
├── docker-compose.dev.yml      # Development orchestration
├── .venv/                      # Shared virtual environment
└── nichebench/                 # Python service (self-contained)
    ├── Dockerfile              # Production build configuration
    ├── pyproject.toml          # Poetry project config
    ├── tests/                  # Service tests
    └── src/                    # Python src layout
        └── nichebench/         # Actual Python package
            ├── __init__.py
            ├── main.py         # CLI entry point
            ├── tasks/          # Framework-specific tasks
            └── metrics/        # Custom evaluation metrics
```

## Available Tasks

| Task | Framework | Type | Description |
|------|-----------|------|-------------|
| `nichebench_drupal_quiz` | Drupal 10/11 | Quiz | Knowledge assessment for Drupal development |
| `nichebench_drupal_code_generation` | Drupal 10/11 | Code Generation | Generate working Drupal modules |
| `nichebench_drupal_bug_fixing` | Drupal 10/11 | Bug Fixing | Fix broken Drupal code and patterns |

*Note: Additional frameworks can be added by simply creating new subdirectories in `src/nichebench/tasks/` with their task definitions.*

## Development

### Building the Service

```bash
# Build the nichebench service
docker compose build

# Subsequent builds are faster due to layer caching
docker compose build  # ~1 minute after dependencies are cached
```

### Running Tests

```bash
# Run all tests
docker compose run --rm app poetry run pytest

# Run with coverage
docker compose run --rm app poetry run pytest --cov=nichebench

# Run specific test file
docker compose run --rm app poetry run pytest tests/test_tasks.py
```

### Code Quality

We use several tools to maintain code quality:

```bash
# Format code
docker compose run --rm app poetry run black .

# Sort imports
docker compose run --rm app poetry run isort .

# Lint code
docker compose run --rm app poetry run flake8 .

# Type checking
docker compose run --rm app poetry run mypy nichebench/

# Run all checks
docker compose run --rm app poetry run pre-commit run --all-files
```

### Adding New Tasks

1. Create a new task file in `nichebench/tasks/`
2. Inherit from `NicheBenchTask`
3. Implement required methods
4. Register with `@register_task` decorator
5. Add tests in `tests/`

Example:

```python
from nichebench.tasks import NicheBenchTask, register_task

@register_task
class MyFrameworkTask(NicheBenchTask):
    def __init__(self):
        super().__init__(
            task_name="my_framework_task",
            dataset_name="nichebench/my-framework-dataset"
        )

    def get_prompt(self, line: Dict) -> str:
        # Implementation here
        pass

    @property
    def checklist(self) -> List[str]:
        return ["item1", "item2", "item3"]
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request

### Pre-commit Hooks

We use pre-commit hooks to ensure code quality:

- **Black** for code formatting
- **isort** for import sorting
- **flake8** for linting
- **mypy** for type checking
- **Conventional commits** for commit message format

## Status: DONE ✅

- [x] **CLI Framework** - Typer-based CLI with rich output, working commands
- [x] **Poetry Setup** - Local development with virtual environment
- [x] **LightEval Integration** - Task configuration and metric system working
- [x] **Auto-Discovery Framework System** - Dynamic framework detection and loading
- [x] **Hardcoded Sample Data** - Development-ready tasks with checklist-based evaluation
- [x] **Dynamic Metrics** - Custom checklist evaluation using LightEval extensions
- [x] **Development Environment** - Setup script, Makefile, pre-commit hooks

### Future Versions

- [ ] 📅 Additional framework support (Svelte, Laravel, etc.)
- [ ] 📅 Advanced evaluation metrics
- [ ] 📅 Leaderboard publishing
- [ ] 📅 Web interface
- [ ] 📅 CI/CD pipelines

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built on top of [LightEval](https://github.com/huggingface/lighteval)
- Inspired by framework-specific AI evaluation needs
- Community feedback and contributions
# nichebench
