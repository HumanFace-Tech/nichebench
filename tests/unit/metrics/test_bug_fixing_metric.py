from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from nichebench.metrics.bug_fixing_metric import DeepEvalBugFixingMetric


class MockBugJudge:
    def score_bug_fixing(
        self,
        *,
        bug_description,
        proposed_fix,
        checklist,
        model="mock",
        model_params=None,
        system_prompt=None,
        judge_notes=None,
    ):
        # Mock logic: evaluate each criterion based on keywords in the fix
        criteria_results = []

        for criterion in checklist:
            criterion_lower = criterion.lower()
            fix_lower = proposed_fix.lower()
            passes = False
            explanation = "Mock evaluation"

            if "avoid" in criterion_lower and "drupal_set_message" in criterion_lower:
                # Check if fix avoids deprecated functions - smart parsing
                # Look for the pattern where deprecated function is shown but replaced
                lines = proposed_fix.split("\n")
                has_before_after = any("before" in line.lower() for line in lines)
                has_replacement = "messenger" in fix_lower
                # If it's a before/after example with replacement, that's good
                # If it just uses the deprecated function without context, that's bad
                if has_before_after and has_replacement:
                    passes = True
                else:
                    # Simple check: does it mention the deprecated function without context?
                    passes = "drupal_set_message" not in fix_lower
                explanation = f"Avoids drupal_set_message() properly: {passes}"
            elif "drupal 11" in criterion_lower:
                # Check if fix mentions modern Drupal patterns
                passes = "messenger" in fix_lower or "\\drupal::" in fix_lower
                explanation = f"Uses Drupal 11 compatible APIs: {passes}"
            elif "service" in criterion_lower:
                # Check if fix uses proper service usage - look for DI pattern
                passes = "dependency injection" in fix_lower or "$this->messenger" in fix_lower
                explanation = f"Uses proper service pattern: {passes}"
            else:
                # Default: pass if fix has substantial content
                passes = len(proposed_fix.strip()) > 20
                explanation = f"Has substantial fix: {passes}"

            criteria_results.append({"criterion": criterion, "pass": passes, "explanation": explanation})

        # Calculate overall score
        passed = sum(1 for c in criteria_results if c["pass"])
        total = len(criteria_results) if criteria_results else 1
        overall_score = passed / total

        return {
            "criteria": criteria_results,
            "overall_score": overall_score,
            "summary": f"Mock evaluation: {passed}/{total} criteria passed",
            "raw": "mocked judge response",
        }


def test_deepeval_bug_fixing_metric_integration():
    """Test the bug fixing metric with a passing scenario."""
    # Build a test case with a good fix
    fix_output = """
Replace the deprecated drupal_set_message() with the messenger service:

// Before:
drupal_set_message('Article created successfully!');

// After:
\\Drupal::messenger()->addMessage('Article created successfully!');

Or with dependency injection:
$this->messenger->addMessage('Article created successfully!');
    """

    tc = LLMTestCase(
        input="Fix deprecated drupal_set_message() usage",
        actual_output=fix_output,
        expected_output="",  # Not used for bug fixing
    )

    # Add checklist as attribute (this would normally come from YAML)
    setattr(
        tc,
        "checklist",
        ["Must avoid drupal_set_message()", "Must work on Drupal 11", "Should mention proper service usage"],
    )

    metric = DeepEvalBugFixingMetric(judge=MockBugJudge(), model="mock")

    # Manually measure first to get score
    score = metric.measure(tc)
    assert score == 1.0
    assert metric.success

    # Then test with deepeval (which may re-run measure)
    assert_test(tc, [metric])


def test_deepeval_bug_fixing_metric_failure():
    """Test the bug fixing metric with a failing scenario."""
    # Build a test case with a bad fix that still uses deprecated API
    fix_output = "Just add some comments to the drupal_set_message() call"

    tc = LLMTestCase(input="Fix deprecated drupal_set_message() usage", actual_output=fix_output, expected_output="")

    setattr(tc, "checklist", ["Must avoid drupal_set_message()", "Must work on Drupal 11"])

    metric = DeepEvalBugFixingMetric(judge=MockBugJudge(), model="mock", threshold=0.5)

    # Should fail since our mock judge requires avoiding deprecated API
    score = metric.measure(tc)
    assert score == 0.0
    assert not metric.success


def test_deepeval_bug_fixing_metric_partial():
    """Test the bug fixing metric with partial success."""
    fix_output = """
Use \\Drupal::messenger()->addMessage() instead of the deprecated function.
This will work in Drupal 11.
    """

    tc = LLMTestCase(input="Fix deprecated API usage", actual_output=fix_output, expected_output="")

    setattr(
        tc,
        "checklist",
        [
            "Must avoid drupal_set_message()",  # This will pass
            "Should mention proper service usage",  # This will fail (no DI mentioned)
        ],
    )

    metric = DeepEvalBugFixingMetric(judge=MockBugJudge(), model="mock", threshold=0.4)

    # Should get 0.5 score (1/2 criteria pass) and succeed with low threshold
    score = metric.measure(tc)
    assert score == 0.5
    assert metric.success  # Because 0.5 >= 0.4 threshold


def test_bug_fixing_metric_judge_response():
    """Test that the judge response is properly stored."""
    tc = LLMTestCase(
        input="Fix some bug",
        actual_output="Use \\Drupal::messenger()->addMessage() for better compatibility",
        expected_output="",
    )
    setattr(tc, "checklist", ["Uses modern API"])

    metric = DeepEvalBugFixingMetric(judge=MockBugJudge(), model="mock")
    metric.measure(tc)

    # Check that judge response was stored
    assert metric.last_judge_response is not None
    assert "criteria" in metric.last_judge_response
    assert "overall_score" in metric.last_judge_response
    assert "summary" in metric.last_judge_response
    assert len(metric.last_judge_response["criteria"]) == 1
