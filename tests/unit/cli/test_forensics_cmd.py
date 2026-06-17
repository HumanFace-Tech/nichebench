"""CLI tests for the `nichebench forensics` command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nichebench.cli.app import app

runner = CliRunner()


def _make_trial_dir(base: Path) -> Path:
    """Create a minimal trial directory accepted by collect_reports."""
    trial = base / "trial_1"
    trial.mkdir(parents=True)
    (trial / "metadata.json").write_text(
        json.dumps({"trial": 1, "mut_model_binding": "dummy-model"}),
        encoding="utf-8",
    )
    return trial


class TestForensicsCommand:
    def test_success_text_output(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        result = runner.invoke(app, ["forensics", "--path", str(trial)])
        assert result.exit_code == 0, result.output
        assert "Trial 1/1" in result.output

    def test_success_json_output(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        result = runner.invoke(app, ["forensics", "--path", str(trial), "--json"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["model"] == "dummy-model"

    def test_invalid_path_exits_nonzero(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        result = runner.invoke(app, ["forensics", "--path", str(missing)])
        assert result.exit_code != 0

    def test_empty_dir_exits_nonzero(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty-dir"
        empty.mkdir()
        result = runner.invoke(app, ["forensics", "--path", str(empty)])
        assert result.exit_code != 0
