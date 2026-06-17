"""Category routing for NicheBench test execution.

This module owns:
    - execute_test: category routing for runtime vs non-runtime execution

This module does NOT own:
    - TestExecutor orchestration (see orchestrator.py)
    - Parallel execution (see parallel.py)
    - Summary aggregation (see summary.py)
    - Result persistence (see persistence.py)
    - Runtime execution (see RuntimeExecutionMixin.execute_runtime_test)
"""

from typing import TYPE_CHECKING, Any, Callable, Optional

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.result import TestResult

if TYPE_CHECKING:
    from nichebench.execution.runners.judge import JudgeRunner
    from nichebench.execution.runners.mut import MUTRunner


def execute_test(
    test_case: TestCaseSpec,
    category: str,
    framework: str,
    mut_model_str: str,
    judge_model_str: str,
    mut_runner: "MUTRunner",
    judge_runner: "JudgeRunner",
    system_prompt: Optional[str],
    judge_system_prompt: Optional[str],
    runner: Any = None,
    trial: int = 0,
    execute_runtime_test_fn: Optional[Callable[..., Any]] = None,
) -> TestResult:
    """Execute a single test case with category-aware routing.

    For ``runtime`` tasks, delegates to ``execute_runtime_test_fn``.
    Otherwise runs the MUT then the judge sequentially and returns a
    TestResult with populated mut_output and judge_output.

    Args:
        test_case: Test case specification.
        category: Task category ("quiz", "code_generation", "bug_fixing", "runtime").
        framework: Framework name.
        mut_model_str: MUT model string.
        judge_model_str: Judge model string.
        mut_runner: MUT runner instance.
        judge_runner: Judge runner instance.
        system_prompt: MUT system prompt text.
        judge_system_prompt: Judge system prompt text.
        runner: Optional progress runner for status updates.
        trial: Trial number (default 0).
        execute_runtime_test_fn: Optional callable for runtime task execution.

    Returns:
        TestResult with populated mut_output and judge_output.
    """
    if category == "runtime":
        if runner:
            runner.update_test_status(
                f"[yellow]🧪 {test_case.id}[/yellow] - Running runtime orchestration (trial {trial + 1})...", 1
            )
        if execute_runtime_test_fn:
            return execute_runtime_test_fn(test_case, trial=trial)
        raise ValueError("runtime category requires execute_runtime_test_fn")

    result = TestResult(framework, category, test_case, mut_model_str, judge_model_str)

    try:
        if runner:
            runner.update_test_status(f"[yellow]🧪 {test_case.id}[/yellow] - Running MUT ({mut_model_str})...", 1)

        mut_output, user_input = mut_runner.run_test(test_case, system_prompt, category, runner)

        result.user_input = user_input
        result.mut_output = mut_output

        if "[Error:" in mut_output:
            result.judge_output = {"error": "MUT failed", "raw": mut_output}
            result.passed = False
            return result

        if runner:
            runner.update_test_status(f"[yellow]🧪 {test_case.id}[/yellow] - Running Judge ({judge_model_str})...", 2)

        judge_output, passed = judge_runner.evaluate_test(
            test_case, category, user_input, mut_output, judge_system_prompt
        )

        result.judge_output = judge_output
        result.passed = passed

    except Exception as e:
        result.error = str(e)
        result.mut_output = f"[Error: {str(e)}]"
        result.judge_output = {"error": str(e)}
        result.passed = False

    return result
