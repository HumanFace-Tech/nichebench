"""Unit tests for nichebench.metrics.rubric — score recomputation and clamping.

The headline invariant: the judge's own ``overall_score`` is audit-only.
The scored value always comes from ``criteria_to_score`` over the
checklist, positionally.
"""

import math

import pytest

from nichebench.metrics.rubric import criteria_to_score, normalize_score


def _criteria(*passes) -> list[dict]:
    return [{"criterion": f"c{i}", "pass": p, "explanation": "x"} for i, p in enumerate(passes)]


class TestCriteriaToScore:
    def test_all_true_is_one(self):
        checklist = ["a", "b", "c"]
        assert criteria_to_score(_criteria(True, True, True), checklist) == pytest.approx(1.0)

    def test_all_false_scores_zero_even_if_judge_claims_high(self):
        """The headline fix: judge reports 0.95, all criteria failed → 0.0."""
        judge_output = {"criteria": _criteria(False, False), "overall_score": 0.95}
        assert criteria_to_score(judge_output["criteria"], ["a", "b"]) == pytest.approx(0.0)
        # The judge's own number is never consulted by the recomputation.
        assert judge_output["overall_score"] == 0.95

    def test_mixed_true_partial_false(self):
        # 2 true + 1 partial + 1 false of 4 → (1 + 1 + 0.5 + 0) / 4
        criteria = _criteria(True, True, "partial", False)
        assert criteria_to_score(criteria, ["a", "b", "c", "d"]) == pytest.approx(0.625)

    def test_missing_criterion_scores_zero_for_that_slot(self):
        # Only one criterion returned for a two-item checklist → slot 2 = 0.0
        criteria = _criteria(True)
        assert criteria_to_score(criteria, ["a", "b"]) == pytest.approx(0.5)

    def test_extra_criteria_are_ignored(self):
        # 4 criteria for a 2-item checklist: extras must not inflate the score.
        criteria = _criteria(True, True, True, True)
        assert criteria_to_score(criteria, ["a", "b"]) == pytest.approx(1.0)

        criteria = _criteria(False, False, True, True)
        assert criteria_to_score(criteria, ["a", "b"]) == pytest.approx(0.0)

    def test_empty_checklist_returns_none(self):
        assert criteria_to_score(_criteria(True), []) is None
        assert criteria_to_score([], []) is None

    def test_non_list_criteria_returns_none(self):
        assert criteria_to_score("not a list", ["a"]) is None
        assert criteria_to_score(None, ["a"]) is None

    def test_positional_matching_ignores_criterion_text(self):
        # Slot order decides, not the criterion names.
        criteria = [
            {"criterion": "second", "pass": False, "explanation": "x"},
            {"criterion": "first", "pass": True, "explanation": "x"},
        ]
        assert criteria_to_score(criteria, ["first", "second"]) == pytest.approx(0.5)

    def test_non_dict_criterion_entry_scores_zero(self):
        criteria = ["not a dict", {"criterion": "b", "pass": True}]
        assert criteria_to_score(criteria, ["a", "b"]) == pytest.approx(0.5)

    def test_unknown_pass_values_score_zero(self):
        criteria = _criteria("yes", 1, None, "true")
        assert criteria_to_score(criteria, ["a", "b", "c", "d"]) == pytest.approx(0.0)


class TestNormalizeScore:
    def test_in_range_float_passes_through(self):
        assert normalize_score(0.8) == pytest.approx(0.8)
        assert normalize_score(0) == pytest.approx(0.0)
        assert normalize_score(1) == pytest.approx(1.0)

    def test_out_of_range_is_clamped(self):
        assert normalize_score(1.2) == pytest.approx(1.0)
        assert normalize_score(-0.2) == pytest.approx(0.0)

    def test_numeric_string_is_rejected(self):
        assert normalize_score("0.8") is None

    def test_non_finite_is_rejected(self):
        assert normalize_score(math.nan) is None
        assert normalize_score(math.inf) is None
        assert normalize_score(-math.inf) is None

    def test_bool_is_rejected(self):
        assert normalize_score(True) is None
        assert normalize_score(False) is None

    def test_non_numeric_is_rejected(self):
        assert normalize_score(None) is None
        assert normalize_score("garbage") is None
        assert normalize_score([0.5]) is None
