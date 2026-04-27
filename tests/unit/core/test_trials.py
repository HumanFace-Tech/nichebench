"""Unit tests for multi-trial aggregation logic in update_summary / execute_tests_parallel."""
from __future__ import annotations

import math
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.executor import TestExecutor, TestResult

# ── Helpers ─────────────────────────────────────────────────────────────────


def _aggregate_trials(scores_by_id: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    """Replicate the per-task aggregation logic from update_summary."""
    stats = []
    for test_id, scores in scores_by_id.items():
        k = len(scores)
        mean = sum(scores) / k
        if k > 1:
            variance = sum((s - mean) ** 2 for s in scores) / (k - 1)
            std = math.sqrt(variance)
            ci95 = 1.96 * std / math.sqrt(k)
        else:
            std = 0.0
            ci95 = None
        stats.append(
            {
                "test_id": test_id,
                "k": k,
                "score_mean": round(mean, 4),
                "score_std": round(std, 4),
                "score_ci95": round(ci95, 4) if ci95 is not None else None,
            }
        )
    return stats


# ── Tests ───────────────────────────────────────────────────────────────────


def test_single_trial_no_std():
    stats = _aggregate_trials({"task_001": [0.5]})
    assert len(stats) == 1
    assert stats[0]["score_mean"] == 0.5
    assert stats[0]["score_std"] == 0.0
    assert stats[0]["score_ci95"] is None
    assert stats[0]["k"] == 1


def test_two_trials_mean_and_std():
    stats = _aggregate_trials({"task_001": [0.4, 0.6]})
    s = stats[0]
    assert s["score_mean"] == pytest.approx(0.5, abs=1e-4)
    assert s["score_std"] == pytest.approx(math.sqrt(0.02), abs=1e-4)
    assert s["score_ci95"] is not None


def test_three_trials_perfect_scores():
    stats = _aggregate_trials({"task_001": [1.0, 1.0, 1.0]})
    s = stats[0]
    assert s["score_mean"] == 1.0
    assert s["score_std"] == 0.0
    assert s["score_ci95"] == 0.0


def test_multiple_tasks_aggregated_independently():
    scores = {"task_001": [0.2, 0.4], "task_002": [0.8, 1.0]}
    stats = {s["test_id"]: s for s in _aggregate_trials(scores)}
    assert stats["task_001"]["score_mean"] == pytest.approx(0.3, abs=1e-4)
    assert stats["task_002"]["score_mean"] == pytest.approx(0.9, abs=1e-4)


def test_k_preserved():
    stats = _aggregate_trials({"task_001": [0.1, 0.2, 0.3, 0.4, 0.5]})
    assert stats[0]["k"] == 5


def test_ci95_formula():
    scores = [0.3, 0.5, 0.7]
    stats = _aggregate_trials({"task_001": scores})
    s = stats[0]
    k = 3
    mean = sum(scores) / k
    var = sum((x - mean) ** 2 for x in scores) / (k - 1)
    std = math.sqrt(var)
    expected_ci95 = 1.96 * std / math.sqrt(k)
    assert s["score_ci95"] == pytest.approx(round(expected_ci95, 4), abs=1e-4)


# ── Multi-trial metadata tests ───────────────────────────────────────────────


def _make_executor_for_trials():
    """Build a minimal TestExecutor for multi-trial metadata tests."""
    mut_cfg = {"provider": "openai", "model": "gpt-4o", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-4o", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

    with (
        patch("nichebench.core.executor.get_config") as mock_config,
        patch.object(TestExecutor, "_load_system_prompt", return_value=None),
        patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
    ):
        mock_config.return_value.get_evaluation_config.return_value = {}
        mock_config.return_value.get_model_string.side_effect = lambda cfg: (f"{cfg['provider']}/{cfg['model']}")
        executor = TestExecutor(
            framework="drupal_runtime",
            category="runtime",
            mut_config=mut_cfg,
            judge_config=judge_cfg,
            network_config=network_cfg,
        )
    return executor


def test_execute_tests_parallel_sequential_sets_trial_metadata():
    """In sequential mode, result.trial is 1-based and result.trials_total equals trials."""
    executor = _make_executor_for_trials()
    tc = TestCaseSpec(id="task_001", type="runtime", raw={})

    def _fake_execute(test_case, runner=None, trial=0):
        return TestResult("drupal_runtime", "runtime", test_case, "openai/gpt-4o", "openai/gpt-4o")

    with patch.object(executor, "execute_test", side_effect=_fake_execute):
        results = executor.execute_tests_parallel([tc], trials=3)

    assert len(results) == 3
    assert results[0].trial == 1
    assert results[1].trial == 2
    assert results[2].trial == 3
    assert all(r.trials_total == 3 for r in results)


def test_execute_tests_parallel_sequential_single_trial_metadata():
    """Single trial run still sets trial=1 and trials_total=1."""
    executor = _make_executor_for_trials()
    tc = TestCaseSpec(id="task_001", type="runtime", raw={})

    def _fake_execute(test_case, runner=None, trial=0):
        return TestResult("drupal_runtime", "runtime", test_case, "openai/gpt-4o", "openai/gpt-4o")

    with patch.object(executor, "execute_test", side_effect=_fake_execute):
        results = executor.execute_tests_parallel([tc], trials=1)

    assert len(results) == 1
    assert results[0].trial == 1
    assert results[0].trials_total == 1
