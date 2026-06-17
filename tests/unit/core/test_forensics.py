"""Unit tests for nichebench.execution.diagnostics.forensics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nichebench.execution.diagnostics import collect_reports, format_text_report

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_trial_dir(base: Path, name: str = "trial_1") -> Path:
    """Create a minimal trial directory with a metadata.json marker."""
    trial = base / name
    trial.mkdir(parents=True)
    _write_json(trial / "metadata.json", {"trial": 1, "mut_model_binding": "test-model"})
    return trial


def _make_run_dir(base: Path) -> Path:
    """Create a minimal run directory with a runtime/test_id/trial_1 tree."""
    run = base / "run-abc"
    run.mkdir(parents=True)
    trial_dir = run / "runtime" / "drupal_runtime_001" / "trial_1"
    trial_dir.mkdir(parents=True)
    _write_json(trial_dir / "metadata.json", {"trial": 1, "mut_model_binding": "run-model"})
    # Minimal details.jsonl
    details = run / "details.jsonl"
    row = {
        "test_id": "drupal_runtime_001",
        "trial": 1,
        "mut_model": "run-model",
        "deterministic_score": 0.75,
        "judge_score": 0.8,
        "final_score": 0.775,
        "judge_output": {},
    }
    details.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return run


# ---------------------------------------------------------------------------
# collect_reports — basic behaviour
# ---------------------------------------------------------------------------


class TestCollectReports:
    def test_trial_dir_returns_single_report(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        reports = collect_reports(trial)
        assert len(reports) == 1

    def test_trial_report_has_expected_keys(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        report = collect_reports(trial)[0]
        for key in (
            "trial_path",
            "model",
            "test_id",
            "run_id",
            "trial_index",
            "deterministic_score",
            "judge_score",
            "hybrid_score",
            "final_score",
            "trajectory_messages",
            "tool_calls_total",
            "tool_calls_noncompleted",
            "tool_status_counts",
            "last_assistant_finish_reason",
            "artifacts",
        ):
            assert key in report, f"Key '{key}' missing from report"

    def test_trial_dir_model_populated_from_metadata(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        report = collect_reports(trial)[0]
        assert report["model"] == "test-model"

    def test_run_dir_returns_one_report_per_trial(self, tmp_path: Path) -> None:
        run = _make_run_dir(tmp_path)
        reports = collect_reports(run)
        assert len(reports) == 1

    def test_run_dir_scores_from_details_jsonl(self, tmp_path: Path) -> None:
        run = _make_run_dir(tmp_path)
        report = collect_reports(run)[0]
        assert report["deterministic_score"] == pytest.approx(0.75)
        assert report["judge_score"] == pytest.approx(0.8)

    def test_unknown_path_returns_empty_list(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "not-a-trial"
        empty_dir.mkdir()
        reports = collect_reports(empty_dir)
        assert reports == []


# ---------------------------------------------------------------------------
# Tool-call / trajectory analysis
# ---------------------------------------------------------------------------


class TestTrajectoryAnalysis:
    def _trial_with_trajectory(self, base: Path, trajectory: object) -> Path:
        trial = _make_trial_dir(base)
        _write_json(trial / "trajectory.json", trajectory)
        return trial

    def test_detects_completed_tool_calls(self, tmp_path: Path) -> None:
        traj = {
            "messages": [
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "tool", "state": {"status": "completed"}},
                        {"type": "tool", "state": {"status": "completed"}},
                    ],
                }
            ]
        }
        trial = self._trial_with_trajectory(tmp_path, traj)
        report = collect_reports(trial)[0]
        assert report["tool_calls_total"] == 2
        assert report["tool_calls_noncompleted"] == 0
        assert report["tool_status_counts"] == {"completed": 2}

    def test_detects_non_completed_tool_calls(self, tmp_path: Path) -> None:
        traj = {
            "messages": [
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "tool", "state": {"status": "completed"}},
                        {"type": "tool", "state": {"status": "error"}},
                    ],
                }
            ]
        }
        trial = self._trial_with_trajectory(tmp_path, traj)
        report = collect_reports(trial)[0]
        assert report["tool_calls_total"] == 2
        assert report["tool_calls_noncompleted"] == 1
        assert report["tool_status_counts"].get("error") == 1

    def test_last_finish_reason_from_step_finish_part(self, tmp_path: Path) -> None:
        traj = {
            "messages": [
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "step-finish", "finishReason": "stop"},
                    ],
                }
            ]
        }
        trial = self._trial_with_trajectory(tmp_path, traj)
        report = collect_reports(trial)[0]
        assert report["last_assistant_finish_reason"] == "stop"

    def test_no_trajectory_file_leaves_fields_none(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        # No trajectory.json written
        report = collect_reports(trial)[0]
        assert report["trajectory_messages"] is None
        assert report["tool_calls_total"] is None


# ---------------------------------------------------------------------------
# Missing file resilience
# ---------------------------------------------------------------------------


class TestMissingFilesGracefully:
    def test_no_metadata_still_returns_report(self, tmp_path: Path) -> None:
        trial = tmp_path / "trial_1"
        trial.mkdir()
        # Write only run.log to make it a valid trial dir
        (trial / "run.log").write_text("some log", encoding="utf-8")
        reports = collect_reports(trial)
        assert len(reports) == 1
        assert reports[0]["model"] is None

    def test_malformed_json_is_ignored(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        (trial / "trajectory.json").write_text("{bad json!!!", encoding="utf-8")
        reports = collect_reports(trial)
        assert len(reports) == 1
        assert reports[0]["trajectory_messages"] is None

    def test_checks_json_deterministic_score(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        _write_json(
            trial / "checks.json",
            {"deterministic": [{"passed": True}, {"passed": False}, {"passed": True}]},
        )
        report = collect_reports(trial)[0]
        assert report["deterministic_score"] == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# format_text_report
# ---------------------------------------------------------------------------


class TestFormatTextReport:
    def test_empty_reports_returns_empty_string(self) -> None:
        assert format_text_report([]) == ""

    def test_contains_trial_header(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        reports = collect_reports(trial)
        text = format_text_report(reports)
        assert "Trial 1/1" in text

    def test_null_values_rendered_as_null(self, tmp_path: Path) -> None:
        trial = _make_trial_dir(tmp_path)
        reports = collect_reports(trial)
        text = format_text_report(reports)
        # model is "test-model" from metadata; scores should be null
        assert "null" in text
