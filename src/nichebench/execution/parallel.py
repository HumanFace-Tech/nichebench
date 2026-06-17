"""Parallel execution helpers for NicheBench test execution.

This module owns:
    - ThreadSafeRunner: lock-protected progress runner proxy for ThreadPoolExecutor
    - execute_tests_parallel: parallel/sequential test execution driver

This module does NOT own:
    - TestExecutor orchestration (see orchestrator.py)
    - Category routing (see dispatch.py)
    - Result persistence (see persistence.py)
    - Summary aggregation (see summary.py)
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple

from nichebench.execution.result import TestResult

if TYPE_CHECKING:
    from nichebench.cli.rich_views.run_views import LiveTestRunner


class ThreadSafeRunner:
    """Lock-protected progress runner proxy for use in ThreadPoolExecutor.

    Each parallel worker gets its own ThreadSafeRunner that serializes status
    updates through a shared progress lock so the CLI display stays coherent.
    """

    def __init__(self, original_runner: Any, lock: Lock, worker_id: int, test_index: int, total_tests: int):
        """Initialize the thread-safe runner.

        Args:
            original_runner: The underlying LiveTestRunner to proxy.
            lock: Shared Lock for serializing updates.
            worker_id: Worker identifier for parallel display.
            test_index: Index of the current test case.
            total_tests: Total number of test cases.
        """
        self._original = original_runner
        self._lock = lock
        self._worker_id = worker_id
        self._test_index = test_index
        self._total = total_tests

    def update_test_status(self, message: str, step: int) -> None:
        """Update test status in a thread-safe manner.

        Args:
            message: Status message (may contain emoji prefix).
            step: Current step number.
        """
        with self._lock:
            test_id = "test"
            if "🧪" in message:
                parts = message.split("🧪")
                if len(parts) > 1:
                    test_part = parts[1].split("[/yellow]")[0].strip()
                    test_id = test_part

            if "Running MUT" in message:
                status = "Running MUT"
            elif "Running Judge" in message:
                status = "Running Judge"
            else:
                status = "Processing"

            self._original.update_worker_status(self._worker_id, test_id, status, step)

    def advance_progress(self, amount: int) -> None:
        """No-op in thread-safe context; progress is advanced by the main thread."""


def create_thread_safe_runner(
    runner: Any, lock: Lock, parallelism: int, test_index: int, total_tests: int
) -> Optional[ThreadSafeRunner]:
    """Create a lock-protected proxy runner for parallel execution.

    Args:
        runner: The underlying LiveTestRunner to proxy.
        lock: Shared Lock for serializing updates.
        parallelism: Maximum number of concurrent workers.
        test_index: Index of the current test case.
        total_tests: Total number of test cases.

    Returns:
        ThreadSafeRunner instance, or None if runner is None.
    """
    if not runner:
        return None

    worker_id = test_index % parallelism
    return ThreadSafeRunner(runner, lock, worker_id, test_index, total_tests)


def execute_tests_parallel(
    test_cases: List[Any],
    execute_test_fn: Callable[..., TestResult],
    parallelism: int,
    runner: Optional["LiveTestRunner"] = None,
    save_callback: Optional[Callable[[TestResult], None]] = None,
    summary_callback: Optional[Callable[[List[TestResult]], None]] = None,
    trials: int = 1,
    progress_lock: Optional[Lock] = None,
) -> List[TestResult]:
    """Execute multiple test cases with optional parallel workers.

    When ``parallelism == 1`` runs sequentially with early bail-out on
    harness-blocking failures.  Otherwise uses a ``ThreadPoolExecutor``
    to run tests concurrently; results are returned in original submission
    order.  ``save_callback`` and ``summary_callback`` are invoked after
    each result for incremental persistence.

    Args:
        test_cases: List of TestCaseSpec objects to execute.
        execute_test_fn: Callable that executes a single test case.
        parallelism: Maximum concurrent workers (default 1).
        runner: Optional LiveTestRunner for progress display.
        save_callback: Optional callback invoked after each result for persistence.
        summary_callback: Optional callback invoked after each result for summary updates.
        trials: Number of times to run each test case (default 1).
        progress_lock: Optional shared lock for thread-safe progress updates.

    Returns:
        List of TestResult objects in original submission order.
    """
    if progress_lock is None:
        progress_lock = Lock()

    if parallelism == 1:
        return _execute_tests_sequential(
            test_cases=test_cases,
            execute_test_fn=execute_test_fn,
            runner=runner,
            save_callback=save_callback,
            summary_callback=summary_callback,
            trials=trials,
        )

    return _execute_tests_threaded(
        test_cases=test_cases,
        execute_test_fn=execute_test_fn,
        parallelism=parallelism,
        runner=runner,
        save_callback=save_callback,
        summary_callback=summary_callback,
        progress_lock=progress_lock,
        trials=trials,
    )


def _execute_tests_sequential(
    test_cases: List[Any],
    execute_test_fn: Callable[..., TestResult],
    runner: Optional["LiveTestRunner"] = None,
    save_callback: Optional[Callable[[TestResult], None]] = None,
    summary_callback: Optional[Callable[[List[TestResult]], None]] = None,
    trials: int = 1,
) -> List[TestResult]:
    """Sequential test execution with early bail-out on harness-blocking failures."""
    sequential_results: List[TestResult] = []
    harness_failed = False

    for trial_num in range(trials):
        if harness_failed:
            break
        for test_case in test_cases:
            if runner:
                runner.start_test(test_case.id)

            result = execute_test_fn(test_case, runner, trial=trial_num)
            result.trial = trial_num + 1
            result.trials_total = trials
            sequential_results.append(result)

            if save_callback:
                save_callback(result)
            if summary_callback:
                summary_callback(sequential_results)

            if runner:
                runner.finish_test(test_case.id, result.passed, result.error)

            if _is_harness_blocking_failure(result):
                harness_failed = True
                break

    return sequential_results


def _execute_tests_threaded(
    test_cases: List[Any],
    execute_test_fn: Callable[..., TestResult],
    parallelism: int,
    runner: Optional["LiveTestRunner"] = None,
    save_callback: Optional[Callable[[TestResult], None]] = None,
    summary_callback: Optional[Callable[[List[TestResult]], None]] = None,
    progress_lock: Optional[Lock] = None,
    trials: int = 1,
) -> List[TestResult]:
    """Parallel test execution using ThreadPoolExecutor.

    Each (test_case, trial) pair is treated as a separate work item so the
    threaded path produces the same number of ``TestResult`` objects as the
    sequential path.  ``result.trial`` and ``result.trials_total`` are stamped
    before persisting so that downstream summary/categorization code is
    consistent across execution modes.
    """
    if progress_lock is None:
        progress_lock = Lock()

    # Expand to (test_case, trial_num) work items.  trials=1 keeps one entry
    # per test case (matching the historical contract).
    work_items: List[Tuple[Any, int]] = []
    for test_case in test_cases:
        for trial_num in range(trials):
            work_items.append((test_case, trial_num))

    parallel_results: List[Optional[TestResult]] = [None] * len(work_items)
    completed_results: List[TestResult] = []

    def execute_with_index(index_and_work):
        index, (test_case, trial_num) = index_and_work
        safe_runner = create_thread_safe_runner(runner, progress_lock, parallelism, index, len(work_items))
        return index, execute_test_fn(test_case, safe_runner, trial=trial_num)

    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        future_to_index = {executor.submit(execute_with_index, (i, work)): i for i, work in enumerate(work_items)}

        for future in as_completed(future_to_index):
            try:
                index, result = future.result()
            except Exception as exc:  # noqa: BLE001
                # Per-future exception handling: convert unexpected errors into a
                # failed TestResult envelope so the parallel batch does not abort
                # and earlier completed futures can still be persisted.
                index = future_to_index[future]
                test_case, _trial_num = work_items[index]
                origin = self_safe_origin(runner)
                result = TestResult(
                    framework=getattr(origin, "framework", ""),
                    category=getattr(origin, "category", ""),
                    test_case=test_case,
                    mut_model="",
                    judge_model="",
                )
                result.error = f"parallel_executor_exception: {exc}"
                result.passed = False
                result.trial = _trial_num + 1
                result.trials_total = trials

            result.trial = getattr(result, "trial", 0) or 1
            result.trials_total = trials
            parallel_results[index] = result
            completed_results.append(result)

            if save_callback:
                with progress_lock:
                    save_callback(result)

            if summary_callback:
                with progress_lock:
                    summary_callback(list(completed_results))

            if runner:
                with progress_lock:
                    worker_id = index % parallelism
                    runner.finish_worker_test(worker_id, result.test_case.id, result.passed)
                    runner.advance_progress(1)
                    runner.progress.update(
                        runner.main_task,
                        description=f"[cyan]Running {runner.framework}/{runner.category}[/cyan] - "
                        f"✅ {runner.passed_tests} passed, ❌ {runner.failed_tests} failed",
                    )

    final_results: List[TestResult] = [r for r in parallel_results if r is not None]
    return final_results


def self_safe_origin(runner: Any) -> Any:
    """Return the underlying origin (e.g., ``LiveTestRunner``) for safe attribute access.

    Used by the parallel exception handler to recover the framework/category
    fields when synthesising a failed ``TestResult`` from a worker exception.
    """
    origin = getattr(runner, "_original", None)
    if origin is not None:
        return origin
    return runner


def _is_harness_blocking_failure(result: TestResult) -> bool:
    """Return True only for failures that should halt the remaining trial set.

    Blocking failures are early-stage bootstrap/config errors that would
    invalidate every subsequent trial (e.g., workspace setup failure).
    Non-blocking include agent execution errors, network issues, and
    model protocol failures — these are allowed to complete for stability analysis.
    """
    if not result.error:
        return False

    judge_output = result.judge_output or {}
    failure_class = judge_output.get("failure_class")
    first_failed_stage = judge_output.get("first_failed_stage")

    if failure_class in {"agent_execution", "network_connectivity", "model_protocol_compatibility"}:
        return False

    return first_failed_stage in {"config_resolution", "workspace_setup", "environment_bootstrap"}
