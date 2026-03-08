#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# NicheBench Development Environment Setup
#
# This script sets up a local development environment using Poetry
# with a virtual environment in the project directory.
#
# Usage:
#   ./setup.sh [--clean]  # --clean to remove existing .venv
# ------------------------------------------------------------

CLEAN_INSTALL=false
if [[ "${1-}" == "--clean" ]]; then
    CLEAN_INSTALL=true
    echo "🧹 Clean install requested - will remove existing .venv"
fi

echo "🚀 Setting up NicheBench development environment..."

# Check if Python 3.10+ is available
if ! command -v python3 >/dev/null; then
    echo "❌ Python 3 is required but not installed"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "📍 Found Python $PYTHON_VERSION"

if [[ "$(printf '%s\n' "3.10" "$PYTHON_VERSION" | sort -V | head -n1)" != "3.10" ]]; then
    echo "❌ Python 3.10+ required, found $PYTHON_VERSION"
    exit 1
fi

# Install Poetry if not available
if ! command -v poetry >/dev/null; then
    echo "📦 Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
    export PATH="$HOME/.local/bin:$PATH"
    echo "✅ Poetry installed"
else
    echo "✅ Poetry found"
fi

# Configure Poetry to create .venv in project directory
echo "⚙️  Configuring Poetry..."
poetry config virtualenvs.in-project true
poetry config virtualenvs.use-poetry-python false

# Clean install if requested
if [[ "$CLEAN_INSTALL" == true ]] && [[ -d ".venv" ]]; then
    echo "🗑️  Removing existing virtual environment..."
    rm -rf .venv
fi

# Install dependencies
echo "📚 Installing dependencies..."
if [[ ! -f ".venv/pyvenv.cfg" ]]; then
    poetry install --no-interaction --no-ansi
    echo "✅ Dependencies installed"
else
    echo "✅ Virtual environment already exists, updating dependencies..."
    poetry install --no-interaction --no-ansi
fi

# Install pre-commit hooks
if [[ -f ".pre-commit-config.yaml" ]]; then
    echo "🪝 Installing pre-commit hooks..."
    poetry run pre-commit install || echo "⚠️  Pre-commit hooks installation failed (non-fatal)"
    echo "✅ Pre-commit hooks installed"
fi

# Verify installation
echo "🔍 Verifying installation..."
if poetry run python -c "import nichebench; print('✅ NicheBench package importable')" 2>/dev/null; then
    echo "✅ Installation verified successfully"
else
    echo "⚠️  Package import test failed, but this might be normal if package isn't fully implemented yet"
fi

echo ""
echo "🎉 Development environment setup complete!"
echo ""
echo "📋 Next steps:"
echo "   • Activate the environment: poetry shell"
echo "   • Run the CLI: poetry run nichebench --help"
echo "   • Run tests: poetry run pytest"
echo "   • Format code: poetry run black src/ tests/"
echo "   • Type check: poetry run mypy src/"
echo ""
echo "🔧 IDE Setup:"
echo "   • Python interpreter: $(pwd)/.venv/bin/python"
echo "   • Working directory: $(pwd)"

echo ""
