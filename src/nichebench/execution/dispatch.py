"""Category routing for NicheBench test execution.

This module owns:
    - execute_test: category routing for runtime vs non-runtime execution

This module does NOT own:
    - TestExecutor orchestration (see orchestrator.py)
    - Parallel execution (see parallel.py)
    - Summary aggregation (see summary.py)
    - Result persistence (see persistence.py)
    - Runtime execution (see RuntimeExecutionMixin.execute_runtime_test)

The removed ``code_agent`` category is rejected here before any runner is
invoked, so a legacy task can never execute as another type.
"""

from typing import TYPE_CHECKING, Any, Callable, Optional

from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.result import TestResult

if TYPE_CHECKING:
    from nichebench.execution.runners.judge import JudgeRunner
    from nichebench.execution.runners.mut import MUTRunner

# The ``code_agent`` prototype has been removed.  Its tasks must fail loudly
# here, before any runner is invoked, instead of falling through to another
# category's execution path.
REMOVED_CATEGORIES = frozenset({"code_agent"})
REMOVED_CATEGORY_MESSAGE = (
    "Category 'code_agent' has been removed; use the 'runtime' (drupal_runtime) category instead."
)


def ensure_supported_category(category: str) -> None:
    """Reject a removed task category before it reaches any runner.

    Args:
        category: Task category about to be executed.

    Raises:
        ValueError: if ``category`` was removed from the harness.
    """
    if category in REMOVED_CATEGORIES:
        raise ValueError(REMOVED_CATEGORY_MESSAGE)


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

    Every category — ``quiz`` included — is scored by the configured judge,
    so the result carries the real ``judge_model`` and
    ``judge_method="llm"``.

    Args:
        test_case: Test case specification.
        category: Task category ("quiz", "code_generation", "bug_fixing",
            "runtime").  The removed "code_agent" category raises.
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

    Raises:
        ValueError: if ``category`` is the removed ``code_agent`` category.
    """
    ensure_supported_category(category)

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

        # Propagate judge error/attempt metadata when the judge reported it.
        if isinstance(judge_output, dict):
            error_kind = judge_output.get("error_kind")
            if isinstance(error_kind, str):
                result.error_kind = error_kind
            attempts = judge_output.get("attempts")
            if isinstance(attempts, int) and not isinstance(attempts, bool):
                result.judge_attempts = attempts
            # Structural sanity findings (C4) ride along on the judge output
            # so reports can render the ok/advisory/hard indicator.
            sanity_findings = judge_output.get("sanity_findings")
            if isinstance(sanity_findings, list):
                result.sanity_findings = list(sanity_findings)

    except Exception as e:
        result.error = str(e)
        result.mut_output = f"[Error: {str(e)}]"
        # An exception that escapes the runners is a harness/config problem,
        # not a model failure — mark it so reports never treat it as a score.
        result.judge_output = {"error": str(e), "error_kind": "config_error"}
        result.error_kind = "config_error"
        result.passed = False

    return result
