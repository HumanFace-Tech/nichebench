.PHONY: help setup install test lint format type-check clean dev-shell run
.DEFAULT_GOAL := help

help: ## Show this help message
	@echo "NicheBench Development Commands"
	@echo "==============================="
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

setup: ## Set up development environment
	@./setup.sh

setup-clean: ## Set up development environment (clean install)
	@./setup.sh --clean

install: ## Install/update dependencies
	@poetry install

test: ## Run tests
	@poetry run pytest

test-cov: ## Run tests with coverage
	@poetry run pytest --cov=src/nichebench --cov-report=html --cov-report=term

lint: ## Run linting (flake8)
	@poetry run flake8 src/ tests/

format: ## Format code with black and isort
	@poetry run black src/ tests/
	@poetry run isort src/ tests/

format-check: ## Check code formatting without making changes
	@poetry run black --check src/ tests/
	@poetry run isort --check-only src/ tests/

type-check: ## Run type checking with mypy
	@poetry run mypy src/

check-all: format-check lint type-check test ## Run all checks (format, lint, type, test)

clean: ## Clean up build artifacts and caches
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf build/ dist/ htmlcov/ .coverage

dev-shell: ## Activate development shell
	@poetry shell

run: ## Run the CLI application
	@poetry run nichebench

run-help: ## Show CLI help
	@poetry run nichebench --help

nb: ## Quick alias for nichebench (make nb --help)
	@poetry run nichebench $(filter-out $@,$(MAKECMDGOALS))

# This allows make nb --help, make nb list-tasks, etc.
%:
	@:

pre-commit: ## Run pre-commit hooks on all files
	@poetry run pre-commit run --all-files

build: ## Build the package
	@poetry build

publish-test: ## Publish to Test PyPI
	@poetry publish --repository=test-pypi

publish: ## Publish to PyPI
	@poetry publish
