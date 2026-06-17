"""NicheBench test execution orchestrator.

Coordinates parallel and sequential test execution for all task categories
(``quiz``, ``code_generation``, ``bug_fixing``, ``runtime``).  Provides the
top-level ``TestExecutor`` class that sequences MUT runs, optional two-pass
review, deterministic checks, and LLM-judge scoring, then persists results
as a ``.jsonl`` detail stream and a ``summary.json`` aggregate.

This module is the public facade. Helper responsibilities are delegated to:
    - parallel.py: ThreadSafeRunner, execute_tests_parallel
    - summary.py: update_summary and aggregation helpers
    - persistence.py: setup_results_directory, save_incremental_result
    - dispatch.py: execute_test category routing
"""

import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Tuple

import yaml

from nichebench.config.nichebench_config import get_config
from nichebench.config.settings import settings
from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.profiles import resolve_profile
from nichebench.execution.diagnostics.trace import (
    RuntimeTrace,
    classify_runtime_failure,
    first_failed_stage,
)
from nichebench.execution.dispatch import execute_test as dispatch_execute_test
from nichebench.execution.parallel import (
    execute_tests_parallel as parallel_execute_tests,
)
from nichebench.execution.persistence import (
    save_incremental_result as persistence_save_incremental,
)
from nichebench.execution.persistence import (
    setup_results_directory as persistence_setup_results,
)
from nichebench.execution.result import TestResult
from nichebench.execution.runners.judge import JudgeRunner
from nichebench.execution.runners.mut import MUTRunner
from nichebench.execution.runtime import artifacts as runtime_artifacts
from nichebench.execution.runtime import opencode_config as runtime_opencode_config
from nichebench.execution.runtime import trajectory as runtime_trajectory
from nichebench.execution.runtime import wrappers as runtime_wrappers
from nichebench.execution.runtime.executor import RuntimeExecutionMixin
from nichebench.execution.runtime.scoring import (
    CheckResult,
    RuntimeScorer,
    ValidationError,
    validate_runtime_testcase,
)
from nichebench.execution.runtime.workspace import Workspace
from nichebench.execution.summary import update_summary as summary_update_summary
from nichebench.utils.git import find_git_root, resolve_branch_to_sha

_PROMPTS_PATH = Path(__file__).resolve().parent / "runtime" / "prompts" / "executor.yaml"

# Re-export constants from runtime modules for backward compatibility.
_OPENCODE_NATIVE_PROVIDERS = runtime_opencode_config.OPENCODE_NATIVE_PROVIDERS
_PATCH_COMPAT_EXPORTS = (
    json,
    os,
    shutil,
    statistics,
    subprocess,
    sys,
    tempfile,
    time,
    timezone,
    Thread,
    yaml,
    resolve_profile,
    RuntimeTrace,
    classify_runtime_failure,
    first_failed_stage,
    CheckResult,
    RuntimeScorer,
    ValidationError,
    validate_runtime_testcase,
    Workspace,
    runtime_artifacts,
    runtime_trajectory,
    runtime_wrappers,
    find_git_root,
    resolve_branch_to_sha,
)


class TestExecutor(RuntimeExecutionMixin):
    """Main test execution orchestrator with parallel execution support.

    ``TestExecutor`` drives the full evaluation lifecycle: loading per-category
    prompts, running the model-under-test (MUT) and an optional judge, then
    computing a hybrid score (deterministic + LLM).  For ``runtime`` tasks it
    delegates to ``RuntimeExecutionMixin`` which handles workspace setup, cage
    execution, and deterministic checks.
    """

    def __init__(
        self,
        framework: str,
        category: str,
        mut_config: Dict[str, Any],
        judge_config: Dict[str, Any],
        network_config: Dict[str, Any],
        parallelism: int = 1,
        cli_model_override: Optional[str] = None,
    ):
        """Initialize the executor.

        Args:
            framework: Framework name (e.g., ``"drupal"``).
            category: Task category (``"quiz"``, ``"code_generation"``,
                ``"bug_fixing"``, ``"runtime"``).
            mut_config: Model-under-test configuration dict.
            judge_config: Judge model configuration dict.
            network_config: Timeout/retry settings dict.
            parallelism: Maximum concurrent workers (default 1).
            cli_model_override: Raw ``--model`` CLI argument, if explicitly
                provided (suppresses config-level OpenCode model override).
        """
        self.framework = framework
        self.category = category
        self.parallelism = parallelism
        self._cli_model_override = cli_model_override
        self.config = get_config()
        self.evaluation_config = self.config.get_evaluation_config()

        self.mut_model_str = self.config.get_model_string(mut_config)
        self.judge_model_str = self.config.get_model_string(judge_config)

        timeout = network_config.get("timeout", settings.default_timeout)
        retry_attempts = network_config.get("retry_attempts", settings.retry_attempts)
        retry_delay = network_config.get("retry_delay", settings.retry_delay)

        self.mut_runner = MUTRunner(self.mut_model_str, mut_config, timeout, retry_attempts, retry_delay)
        self.judge_runner = JudgeRunner(self.judge_model_str, judge_config, timeout, retry_attempts, retry_delay)

        self.system_prompt = self._load_system_prompt()
        self.judge_system_prompt = self._load_judge_system_prompt()

        self._progress_lock = Lock()
        self.results_outdir: Optional[Path] = None

    def _load_system_prompt(self) -> Optional[str]:
        """Load MUT system prompt for the category."""
        prompt_path = (
            Path(__file__).resolve().parents[3]
            / "frameworks"
            / self.framework
            / "prompts"
            / f"{self.category.upper()}.py"
        )
        return self._import_prompt_var(prompt_path, f"{self.category.upper()}_SYSTEM_PROMPT")

    def _load_judge_system_prompt(self) -> Optional[str]:
        """Load Judge system prompt for the category."""
        judge_path = (
            Path(__file__).resolve().parents[3]
            / "frameworks"
            / self.framework
            / "prompts"
            / "judges"
            / f"JUDGE_{self.category.upper()}.py"
        )
        return self._import_prompt_var(judge_path, f"JUDGE_{self.category.upper()}_SYSTEM_PROMPT")

    def _import_prompt_var(self, mod_path: Path, var_name: str) -> Optional[str]:
        """Import a variable from a Python module."""
        if not mod_path.exists():
            return None

        spec = importlib.util.spec_from_file_location("_prompt_mod", str(mod_path))
        if spec is None or spec.loader is None:
            return None

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, var_name, None)

    def execute_test(self, test_case: TestCaseSpec, runner=None, trial: int = 0) -> TestResult:
        """Execute a single test case.

        For ``runtime`` tasks, delegates to ``execute_runtime_test``.
        Otherwise runs the MUT then the judge sequentially and returns a
        ``TestResult`` with populated ``mut_output`` and ``judge_output``.
        """
        return dispatch_execute_test(
            test_case=test_case,
            category=self.category,
            framework=self.framework,
            mut_model_str=self.mut_model_str,
            judge_model_str=self.judge_model_str,
            mut_runner=self.mut_runner,
            judge_runner=self.judge_runner,
            system_prompt=self.system_prompt,
            judge_system_prompt=self.judge_system_prompt,
            runner=runner,
            trial=trial,
            execute_runtime_test_fn=getattr(self, "execute_runtime_test", None),
        )

    def execute_tests_parallel(
        self, test_cases: List[TestCaseSpec], runner=None, save_callback=None, summary_callback=None, trials: int = 1
    ) -> List[TestResult]:
        """Execute multiple test cases with optional parallel workers.

        When ``parallelism == 1`` runs sequentially with early bail-out on
        harness-blocking failures.  Otherwise uses a ``ThreadPoolExecutor``
        to run tests concurrently; results are returned in original submission
        order.  ``save_callback`` and ``summary_callback`` are invoked after
        each result for incremental persistence.
        """
        return parallel_execute_tests(
            test_cases=test_cases,
            execute_test_fn=self.execute_test,
            parallelism=self.parallelism,
            runner=runner,
            save_callback=save_callback,
            summary_callback=summary_callback,
            trials=trials,
            progress_lock=self._progress_lock,
        )

    def setup_results_directory(self, results_config: Dict[str, Any]) -> Tuple[Path, Path, Path]:
        """Create the results directory tree and store the results_outdir path.

        Directory structure is
        ``results/<framework>/<category>/<model-str>/<timestamp>/``.

        Returns:
            Tuple of (details_path, summary_path, outdir):
            - ``details_path``: ``details.jsonl`` — append-only per-result stream
            - ``summary_path``: ``summary.json`` — aggregate statistics
            - ``outdir``: root results directory
        """
        details_path, summary_path, outdir = persistence_setup_results(
            results_config=results_config,
            framework=self.framework,
            category=self.category,
            mut_model_str=self.mut_model_str,
        )
        self.results_outdir = outdir
        return details_path, summary_path, outdir

    def save_incremental_result(self, result: TestResult, details_path: Path):
        """Append a single TestResult to the details.jsonl file and persist runtime artifacts.

        Args:
            result: Completed ``TestResult`` to serialize.
            details_path: Path to the ``details.jsonl`` file.
        """
        persistence_save_incremental(
            result=result,
            details_path=details_path,
            save_runtime_artifacts_fn=lambda r: self._save_runtime_artifacts(r),
        )

    def update_summary(
        self, results: List[TestResult], summary_path: Path, profile: Optional[str], eval_config: Dict[str, Any]
    ):
        """Compute and write aggregate summary statistics to summary.json.

        Aggregates pass/partial/fail counts and average score across all results,
        then serializes the full summary including model configs and eval config.

        Args:
            results: List of completed ``TestResult`` objects.
            summary_path: Destination path for ``summary.json``.
            profile: Active profile name (or None).
            eval_config: Evaluation configuration dict.
        """
        summary_update_summary(
            results=results,
            summary_path=summary_path,
            category=self.category,
            framework=self.framework,
            mut_model_str=self.mut_model_str,
            judge_model_str=self.judge_model_str,
            mut_runner_model_config=self.mut_runner.model_config,
            judge_runner_model_config=self.judge_runner.model_config,
            profile=profile,
            eval_config=eval_config,
        )
