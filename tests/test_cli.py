"""
Tests for NicheBench CLI.
"""

import pytest
from typer.testing import CliRunner

from nichebench.main import app

runner = CliRunner()


def test_version_command() -> None:
    """Test version command."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "NicheBench v0.1.0" in result.stdout


def test_list_tasks_command() -> None:
    """Test list-tasks command."""
    result = runner.invoke(app, ["list-tasks"])
    assert result.exit_code == 0
    assert "Available Tasks" in result.stdout


def test_run_command_missing_model() -> None:
    """Test run command without required model parameter."""
    result = runner.invoke(app, ["run", "test_task"])
    assert result.exit_code != 0  # Should fail due to missing --model
