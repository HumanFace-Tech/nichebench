from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from nichebench.metrics.code_generation_metric import DeepEvalCodeGenerationMetric


class MockCodeJudge:
    def score_code_generation(
        self,
        *,
        prompt,
        generated_code,
        checklist,
        model="mock",
        model_params=None,
        system_prompt=None,
        judge_notes=None,
    ):
        # More sophisticated mock that evaluates each criterion individually
        criteria_results = []

        for criterion in checklist:
            criterion_lower = criterion.lower()
            passes = False
            explanation = "Mock evaluation"

            if "class" in criterion_lower:
                passes = "class " in generated_code.lower()
                explanation = f"Found class definition: {passes}"
            elif "function" in criterion_lower or "method" in criterion_lower:
                passes = "def " in generated_code.lower()
                explanation = f"Found function/method definition: {passes}"
            elif "syntax" in criterion_lower:
                # Simple syntax check - just check it's not empty
                passes = len(generated_code.strip()) > 0
                explanation = f"Has content: {passes}"
            else:
                # Default: pass if code has basic structure
                passes = "def " in generated_code.lower() or "class " in generated_code.lower()
                explanation = f"Has basic code structure: {passes}"

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


def test_deepeval_code_generation_metric_integration():
    """Test the code generation metric with a passing scenario."""
    # Build a test case with code that should pass
    code_output = """
class MyModule:
    def my_function(self):
        return "hello world"
    """

    tc = LLMTestCase(
        input="Create a simple module with a function",
        actual_output=code_output,
        expected_output="",  # Not used for code generation
    )

    # Add checklist as attribute (this would normally come from YAML)
    setattr(
        tc, "checklist", ["Implements a class structure", "Contains at least one function", "Uses proper Python syntax"]
    )

    metric = DeepEvalCodeGenerationMetric(judge=MockCodeJudge(), model="mock")

    # Manually measure first to get score
    score = metric.measure(tc)
    assert score == 1.0
    assert metric.success

    # Then test with deepeval (which may re-run measure)
    assert_test(tc, [metric])


def test_deepeval_code_generation_metric_failure():
    """Test the code generation metric with a failing scenario."""
    # Build a test case with code that should fail
    code_output = "print('hello')"  # No class or function definition

    tc = LLMTestCase(input="Create a simple module with a class", actual_output=code_output, expected_output="")

    setattr(tc, "checklist", ["Implements a class structure", "Contains proper methods"])

    metric = DeepEvalCodeGenerationMetric(judge=MockCodeJudge(), model="mock", threshold=0.5)

    # Should fail since our mock judge requires class and function
    score = metric.measure(tc)
    assert score == 0.0
    assert not metric.success


def test_deepeval_code_generation_metric_partial():
    """Test the code generation metric with partial success."""
    code_output = """
def my_function():
    return "hello"
    """

    tc = LLMTestCase(input="Create code with class and function", actual_output=code_output, expected_output="")

    setattr(tc, "checklist", ["Has a function", "Has a class"])  # This will fail

    metric = DeepEvalCodeGenerationMetric(judge=MockCodeJudge(), model="mock", threshold=0.4)

    # Should get 0.5 score (1/2 criteria pass) and succeed with low threshold
    score = metric.measure(tc)
    assert score == 0.5
    assert metric.success  # Because 0.5 >= 0.4 threshold


def test_code_generation_metric_judge_response():
    """Test that the judge response is properly stored."""
    tc = LLMTestCase(input="Test prompt", actual_output="class Test: pass", expected_output="")
    setattr(tc, "checklist", ["Has a class"])

    metric = DeepEvalCodeGenerationMetric(judge=MockCodeJudge(), model="mock")
    metric.measure(tc)

    # Check that judge response was stored
    assert metric.last_judge_response is not None
    assert "criteria" in metric.last_judge_response
    assert "overall_score" in metric.last_judge_response
    assert "summary" in metric.last_judge_response
    assert len(metric.last_judge_response["criteria"]) == 1
