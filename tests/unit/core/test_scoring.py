"""Unit tests for RuntimeScorer.compute_hybrid_score."""
from pathlib import Path

import pytest

from nichebench.execution.runtime.scoring import CheckResult, RuntimeScorer


@pytest.fixture()
def scorer(tmp_path: Path) -> RuntimeScorer:
    return RuntimeScorer(workspace_path=tmp_path)


def _check(passed: bool, critical: bool = True) -> CheckResult:
    return CheckResult(name="c", type="t", passed=passed, message="", is_critical=critical)


class TestComputeHybridScore:
    """compute_hybrid_score should honour manifest weight keys correctly."""

    def test_llm_weight_key_used(self, scorer: RuntimeScorer) -> None:
        """Manifests publish llm_weight; it must be read as the judge weight."""
        checks = [_check(True)]
        result = scorer.compute_hybrid_score(
            checks,
            judge_score=0.0,
            scoring_config={"deterministic_weight": 0.5, "llm_weight": 0.5},
        )
        # deterministic=1.0 * 0.5 + judge=0.0 * 0.5 = 0.5
        assert result.deterministic_score == pytest.approx(1.0)
        assert result.judge_score == pytest.approx(0.0)
        assert result.final_score == pytest.approx(0.5)

    def test_legacy_judge_weight_key_still_works(self, scorer: RuntimeScorer) -> None:
        """Legacy judge_weight key must remain honoured as a fallback."""
        checks = [_check(True)]
        result = scorer.compute_hybrid_score(
            checks,
            judge_score=0.0,
            scoring_config={"deterministic_weight": 0.5, "judge_weight": 0.5},
        )
        assert result.final_score == pytest.approx(0.5)

    def test_llm_weight_takes_precedence_over_judge_weight(self, scorer: RuntimeScorer) -> None:
        """If both keys are present, llm_weight wins."""
        checks = [_check(True)]
        result = scorer.compute_hybrid_score(
            checks,
            judge_score=0.0,
            # llm_weight=0.5 should win over judge_weight=0.9
            scoring_config={"deterministic_weight": 0.5, "llm_weight": 0.5, "judge_weight": 0.9},
        )
        assert result.final_score == pytest.approx(0.5)

    def test_no_judge_score_deterministic_only(self, scorer: RuntimeScorer) -> None:
        """When judge_score is None the final score equals the deterministic score."""
        checks = [_check(True), _check(False)]
        result = scorer.compute_hybrid_score(checks, judge_score=None)
        assert result.deterministic_score == pytest.approx(0.5)
        assert result.judge_score is None
        assert result.final_score == pytest.approx(0.5)

    def test_critical_failure_overrides_passed(self, scorer: RuntimeScorer) -> None:
        """A single critical failure must force passed=False regardless of score."""
        checks = [_check(True), _check(False, critical=True)]
        result = scorer.compute_hybrid_score(
            checks,
            judge_score=1.0,
            scoring_config={"deterministic_weight": 0.5, "llm_weight": 0.5, "threshold": 0.1},
        )
        assert result.passed is False

    def test_manifest_weights_50_50(self, scorer: RuntimeScorer) -> None:
        """Verify the exact 50/50 blend used by all five runtime manifests."""
        checks = [_check(True)] * 3 + [_check(False)]  # 75% deterministic
        result = scorer.compute_hybrid_score(
            checks,
            judge_score=0.8,
            scoring_config={"deterministic_weight": 0.5, "llm_weight": 0.5},
        )
        expected = 0.75 * 0.5 + 0.8 * 0.5  # = 0.775
        assert result.final_score == pytest.approx(expected)


class TestRequiredCommandMatching:
    def test_run_log_fallback_matches_actual_bash_command(self, tmp_path: Path) -> None:
        run_log = tmp_path / "run.log"
        run_log.write_text(
            "STDOUT:\n\x1b[0m$ \x1b[0mUSER=app ddev drush cex --yes 2>&1 | tail -1\n../config/sync\n",
            encoding="utf-8",
        )
        scorer = RuntimeScorer(workspace_path=tmp_path, run_log_path=run_log)

        [result] = scorer.run_deterministic_checks(
            [{"name": "cex", "type": "required_command", "command": "*ddev drush cex --yes*"}]
        )

        assert result.passed is True

    def test_run_log_fallback_ignores_assistant_summaries(self, tmp_path: Path) -> None:
        run_log = tmp_path / "run.log"
        run_log.write_text("STDOUT:\n- Ran: ddev drush cex --yes\n", encoding="utf-8")
        scorer = RuntimeScorer(workspace_path=tmp_path, run_log_path=run_log)

        [result] = scorer.run_deterministic_checks(
            [{"name": "cex", "type": "required_command", "command": "*ddev drush cex --yes*"}]
        )

        assert result.passed is False
