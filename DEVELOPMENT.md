# NicheBench Development Guide

This guide explains how to set up and work with the NicheBench project locally.

## Prerequisites

- **Python 3.10+** - The project requires Python 3.10 or higher
- **Poetry** - For dependency management (will be installed automatically if needed)

## Quick Start

1. **Clone and setup the project:**
   ```bash
   git clone <repository-url>
   cd nichebench
   ./setup.sh
   ```

2. **Activate the virtual environment:**
   ```bash
   poetry shell
   ```

3. **Run the CLI:**
   ```bash
   poetry run nichebench --help
   # or just
   nichebench --help  # if you're in the poetry shell
   ```

## Development Workflow

### Environment Management

- **Setup:** `./setup.sh` or `make setup`
- **Clean setup:** `./setup.sh --clean` or `make setup-clean`
- **Activate shell:** `poetry shell` or `make dev-shell`
- **Install dependencies:** `poetry install` or `make install`

### Code Quality

- **Format code:** `make format` (black + isort)
- **Check formatting:** `make format-check`
- **Lint code:** `make lint` (flake8)
- **Type check:** `make type-check` (mypy)
- **Run all checks:** `make check-all`

### Testing

- **Run tests:** `make test` or `poetry run pytest`
- **Run with coverage:** `make test-cov`
- **Watch mode:** `poetry run pytest -f`

### Pre-commit Hooks

Pre-commit hooks are automatically installed during setup. They will:
- Format code with black and isort
- Check for common issues
- Run linting and type checking

To run manually: `make pre-commit`

### Project Structure

```
nichebench/
├── src/                          # Source code (Python src layout)
│   └── nichebench/              # Main package
│       ├── __init__.py
│       ├── __main__.py          # Entry point for python -m nichebench
│       ├── main.py              # CLI application with Typer
│       ├── tasks/               # Framework-specific tasks
│       │   ├── drupal/
│       └── metrics/             # Custom evaluation metrics
├── tests/                       # Test files
├── .venv/                       # Virtual environment (created by Poetry)
├── pyproject.toml              # Project configuration and dependencies
├── poetry.lock                 # Locked dependency versions
├── setup.sh                    # Development environment setup
├── Makefile                    # Development commands
└── README.md                   # Project documentation
```

### Key Files

- **`pyproject.toml`** - Project metadata, dependencies, and tool configuration
- **`poetry.lock`** - Locked dependency versions (commit this!)
- **`setup.sh`** - Automated development environment setup
- **`Makefile`** - Convenient development commands
- **`.pre-commit-config.yaml`** - Code quality hooks

### Adding Dependencies

```bash
# Add a runtime dependency
poetry add package-name

# Add a development dependency
poetry add --group dev package-name

# Add with version constraints
poetry add "package-name>=1.0,<2.0"
```

### Running the CLI

```bash
# Show help
poetry run nichebench --help

# Run specific commands (examples)
poetry run nichebench list-tasks
poetry run nichebench run --task drupal-quiz --model gpt-3.5-turbo
```

### Building and Publishing

```bash
# Build the package
make build

# Publish to Test PyPI
make publish-test

# Publish to PyPI
make publish
```

## IDE Setup

### VS Code

1. **Python Interpreter:** Set to `.venv/bin/python` (should be auto-detected)
2. **Extensions:**
   - Python
   - Pylance
   - Black Formatter
   - isort
   - Error Lens

### PyCharm

1. **Python Interpreter:** Add local interpreter pointing to `.venv/bin/python`
2. **Code style:** Import settings from `pyproject.toml`

## Troubleshooting

### Poetry Issues

```bash
# Clear Poetry cache
poetry cache clear --all

# Reinstall dependencies
./setup.sh --clean

# Check Poetry configuration
poetry config --list
```

### Virtual Environment Issues

```bash
# Remove and recreate virtual environment
rm -rf .venv
./setup.sh
```

### Import Issues

Make sure you're running commands with `poetry run` or have activated the shell with `poetry shell`.

## Contributing

1. **Fork the repository**
2. **Create a feature branch:** `git checkout -b feature/my-feature`
3. **Make changes and add tests**
4. **Run quality checks:** `make check-all`
5. **Commit with conventional commits:** `feat: add new feature`
6. **Push and create a PR**

### Commit Message Format

We use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation changes
- `style:` Code style changes
- `refactor:` Code refactoring
- `test:` Test additions/changes
- `chore:` Maintenance tasks
