"""Clean, elegant test execution engine for NicheBench.

This module provides a unified test execution system with minimal duplication
and clear separation of concerns.
"""

import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from deepeval.test_case import LLMTestCase

from nichebench.config.nichebench_config import get_config
from nichebench.config.settings import settings
from nichebench.core.datamodel import TestCaseSpec
from nichebench.metrics.bug_fixing_metric import DeepEvalBugFixingMetric
from nichebench.metrics.code_generation_metric import DeepEvalCodeGenerationMetric
from nichebench.metrics.deepeval_quiz_metric import DeepEvalQuizMetric
from nichebench.providers.agentic_mut_composer import AgenticMUTPromptComposer
from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import LiteLLMJudge
from nichebench.providers.mut_prompt_composer import MUTPromptComposer
from nichebench.utils.io import ensure_results_dir, save_json, save_jsonl


class TestResult:
    """Encapsulates a single test result."""

    def __init__(self, framework: str, category: str, test_case: TestCaseSpec, mut_model: str, judge_model: str):
        self.framework = framework
        self.category = category
        self.test_case = test_case
        self.mut_model = mut_model
        self.judge_model = judge_model
        self.user_input = ""
        self.mut_output = ""
        self.judge_output: Dict[str, Any] = {}
        self.passed = False
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "framework": self.framework,
            "category": self.category,
            "test_id": self.test_case.id,
            "summary": getattr(self.test_case, "summary", "") or self.test_case.raw.get("summary", ""),
            "mut_model": self.mut_model,
            "judge_model": self.judge_model,
            "input": self.user_input,
            "output": self.mut_output,
            "gold": self.test_case.correct_choice or getattr(self.test_case, "checklist", []),
            "judge_output": self.judge_output,
            "pass": self.passed,
        }


class MUTRunner:
    """Handles MUT (Model Under Test) execution."""

    def __init__(
        self, model_str: str, model_config: Dict[str, Any], timeout: int, retry_attempts: int, retry_delay: int
    ):
        self.model_str = model_str
        self.model_config = model_config
        self.client = LiteLLMClient(timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay)

    def run_test(
        self, test_case: TestCaseSpec, system_prompt: Optional[str], category: str, runner=None
    ) -> Tuple[str, str]:
        """Execute MUT for a test case.

        Returns:
            Tuple of (mut_output, user_input)
        """
        if category == "quiz":
            return self._run_single_turn(test_case, system_prompt, category)
        elif category in ("code_generation", "bug_fixing"):
            return self._run_multi_turn(test_case, system_prompt, category, runner)
        else:
            return self._run_single_turn(test_case, system_prompt, category)

    def _run_single_turn(self, test_case: TestCaseSpec, system_prompt: Optional[str], category: str) -> Tuple[str, str]:
        """Run single-turn conversation (quiz, fallback)."""
        user_input = MUTPromptComposer.compose_prompt(
            test_case=test_case, system_prompt=system_prompt, category=category
        )

        mut_response = self.client.generate(
            prompt=user_input, model=self.model_str, model_params=self.model_config.get("parameters", {})
        )

        output = mut_response.get("output", f"[Error: no output from {self.model_str}]")
        return output, user_input

    def _run_multi_turn(
        self, test_case: TestCaseSpec, system_prompt: Optional[str], category: str, runner=None
    ) -> Tuple[str, str]:
        """Run multi-turn conversation (code generation, bug fixing)."""
        # Start conversation
        if category == "code_generation":
            conversation = AgenticMUTPromptComposer.start_code_conversation(test_case, system_prompt)
        elif category == "bug_fixing":
            conversation = AgenticMUTPromptComposer.start_bug_conversation(test_case, system_prompt)
        else:
            raise ValueError(f"Multi-turn not supported for category: {category}")

        # Execute conversation turns
        messages = conversation._format_for_llm()
        turn_count = 0

        while messages and turn_count < conversation.max_turns:
            turn_count += 1

            if runner:
                runner.update_test_status(f"[yellow]🧪 {test_case.id}[/yellow] - MUT Turn {turn_count}...", 1)

            try:
                mut_response = self.client.generate_with_messages(
                    messages=messages, model=self.model_str, model_params=self.model_config.get("parameters", {})
                )

                assistant_output = mut_response.get("output", f"[Error: no output from {self.model_str}]")

                # Check for MUT error
                if "[Error:" in assistant_output:
                    return assistant_output, "Multi-turn conversation (see conversation manager for full context)"

                # Continue conversation
                messages = conversation.add_assistant_response(assistant_output)

                # Check for conversation errors
                if hasattr(conversation, "has_error") and conversation.has_error:
                    error_msg = f"[Error: Model misbehavior - {conversation.error_reason}]"
                    return error_msg, "Multi-turn conversation (model error occurred)"

                # Check if complete
                if messages is None:
                    break

            except Exception as e:
                return f"[Error: Exception in turn {turn_count}: {str(e)}]", "Multi-turn conversation (error occurred)"

        # Extract final answer
        final_output = (
            conversation.final_answer
            if conversation.is_complete
            else f"[Error: Conversation incomplete after {turn_count} turns]"
        )

        # Get initial user message for logging
        initial_user_message = None
        for turn in conversation.turns:
            if turn.role == "user":
                initial_user_message = turn.content
                break

        return final_output, initial_user_message or "Multi-turn conversation"


class JudgeRunner:
    """Handles Judge evaluation."""

    def __init__(
        self, model_str: str, model_config: Dict[str, Any], timeout: int, retry_attempts: int, retry_delay: int
    ):
        self.model_str = model_str
        self.model_config = model_config
        self.client = LiteLLMClient(timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay)
        self.judge = LiteLLMJudge(client=self.client)

    def evaluate_test(
        self,
        test_case: TestCaseSpec,
        category: str,
        user_input: str,
        mut_output: str,
        judge_system_prompt: Optional[str],
    ) -> Tuple[Dict[str, Any], bool]:
        """Evaluate MUT output using judge.

        Returns:
            Tuple of (judge_output, passed)
        """
        # Create LLMTestCase for DeepEval compatibility
        stc = LLMTestCase(
            input=user_input,
            actual_output=mut_output,
            expected_output=test_case.correct_choice or "",
        )

        # Attach metadata
        if hasattr(test_case, "checklist") and test_case.checklist:
            setattr(stc, "checklist", test_case.checklist)

        if judge_system_prompt:
            setattr(stc, "judge_system_prompt", judge_system_prompt)

        # Create appropriate metric and evaluate
        judge_output: Dict[str, Any] = {}
        passed = False

        if category == "quiz":
            metric = DeepEvalQuizMetric(
                judge=self.judge,
                judge_model=self.model_str,
                judge_params=self.model_config.get("parameters", {}),
            )
            score = metric.measure(stc)
            passed = bool(score)

            if hasattr(metric, "last_judge_response") and metric.last_judge_response is not None:
                judge_output = metric.last_judge_response
            else:
                judge_output = {"score": score, "explanation": "No detailed explanation available"}

        elif category == "code_generation":
            metric = DeepEvalCodeGenerationMetric(
                judge=self.judge,
                judge_model=self.model_str,
                judge_params=self.model_config.get("parameters", {}),
            )
            score = metric.measure(stc)
            passed = bool(score >= 0.7)  # Default threshold

            if hasattr(metric, "last_judge_response") and metric.last_judge_response is not None:
                judge_output = metric.last_judge_response
            else:
                judge_output = {"score": score, "explanation": "No detailed explanation available"}

        elif category == "bug_fixing":
            metric = DeepEvalBugFixingMetric(
                judge=self.judge,
                judge_model=self.model_str,
                judge_params=self.model_config.get("parameters", {}),
            )
            score = metric.measure(stc)
            passed = bool(score >= 0.7)  # Default threshold

            if hasattr(metric, "last_judge_response") and metric.last_judge_response is not None:
                judge_output = metric.last_judge_response
            else:
                judge_output = {"score": score, "explanation": "No detailed explanation available"}

        else:
            # Fallback for unknown categories
            import random

            judge_output = {
                "score": random.choice([0, 1]),
                "explanation": "stub",
                "judge_system_prompt": bool(judge_system_prompt),
            }
            passed = bool(judge_output["score"])

        return judge_output, passed


class TestExecutor:
    """Main test execution orchestrator."""

    def __init__(
        self,
        framework: str,
        category: str,
        mut_config: Dict[str, Any],
        judge_config: Dict[str, Any],
        network_config: Dict[str, Any],
    ):
        self.framework = framework
        self.category = category
        self.config = get_config()

        # Create model strings
        self.mut_model_str = self.config.get_model_string(mut_config)
        self.judge_model_str = self.config.get_model_string(judge_config)

        # Extract network settings
        timeout = network_config.get("timeout", settings.default_timeout)
        retry_attempts = network_config.get("retry_attempts", settings.retry_attempts)
        retry_delay = network_config.get("retry_delay", settings.retry_delay)

        # Create runners
        self.mut_runner = MUTRunner(self.mut_model_str, mut_config, timeout, retry_attempts, retry_delay)
        self.judge_runner = JudgeRunner(self.judge_model_str, judge_config, timeout, retry_attempts, retry_delay)

        # Load prompts
        self.system_prompt = self._load_system_prompt()
        self.judge_system_prompt = self._load_judge_system_prompt()

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

    def execute_test(self, test_case: TestCaseSpec, runner=None) -> TestResult:
        """Execute a single test case."""
        result = TestResult(self.framework, self.category, test_case, self.mut_model_str, self.judge_model_str)

        try:
            # Step 1: Run MUT
            if runner:
                runner.update_test_status(
                    f"[yellow]🧪 {test_case.id}[/yellow] - Running MUT ({self.mut_model_str})...", 1
                )

            mut_output, user_input = self.mut_runner.run_test(test_case, self.system_prompt, self.category, runner)

            result.user_input = user_input
            result.mut_output = mut_output

            # Check for MUT errors
            if "[Error:" in mut_output:
                result.judge_output = {"error": "MUT failed", "raw": mut_output}
                result.passed = False
                return result

            # Step 2: Run Judge
            if runner:
                runner.update_test_status(
                    f"[yellow]🧪 {test_case.id}[/yellow] - Running Judge ({self.judge_model_str})...", 2
                )

            judge_output, passed = self.judge_runner.evaluate_test(
                test_case, self.category, user_input, mut_output, self.judge_system_prompt
            )

            result.judge_output = judge_output
            result.passed = passed

        except Exception as e:
            result.error = str(e)
            result.mut_output = f"[Error: {str(e)}]"
            result.judge_output = {"error": str(e)}
            result.passed = False

        return result

    def setup_results_directory(self, results_config: Dict[str, Any]) -> Tuple[Path, Path, Path]:
        """Setup results directory and return paths."""
        timestamp = datetime.now().strftime(results_config["timestamp_format"])
        outdir = Path("results") / self.framework / self.category / self.mut_model_str.replace("/", "-") / timestamp
        ensure_results_dir(outdir)

        details_path = outdir / "details.jsonl"
        summary_path = outdir / "summary.json"

        return details_path, summary_path, outdir

    def save_incremental_result(self, result: TestResult, details_path: Path):
        """Save a single result incrementally."""
        save_jsonl(details_path, [result.to_dict()], mode="a")

    def update_summary(
        self, results: List[TestResult], summary_path: Path, profile: Optional[str], eval_config: Dict[str, Any]
    ):
        """Update summary statistics."""
        if not results:
            return

        # Categorize results
        def categorize_result(result: TestResult) -> str:
            if self.category in ("code_generation", "bug_fixing"):
                judge_output = result.judge_output
                if isinstance(judge_output, dict):
                    score = judge_output.get("overall_score", 0.0)
                else:
                    score = 1.0 if result.passed else 0.0

                if score > 0.66:
                    return "pass"
                elif score >= 0.33:
                    return "partial"
                else:
                    return "fail"
            else:
                return "pass" if result.passed else "fail"

        # Count categories
        categorized = [categorize_result(r) for r in results]
        passed_count = sum(1 for c in categorized if c == "pass")
        partial_count = sum(1 for c in categorized if c == "partial")
        failed_count = sum(1 for c in categorized if c == "fail")

        # Calculate average score
        total_score = 0.0
        for result in results:
            if self.category in ("code_generation", "bug_fixing"):
                judge_output = result.judge_output
                if isinstance(judge_output, dict):
                    score = judge_output.get("overall_score", 0.0)
                else:
                    score = 1.0 if result.passed else 0.0
            else:
                score = 1.0 if result.passed else 0.0
            total_score += score

        avg_score = total_score / len(results) if results else 0.0

        summary = {
            "framework": self.framework,
            "category": self.category,
            "model": self.mut_model_str,
            "judge": self.judge_model_str,
            "profile": profile,
            "config": {
                "mut": self.mut_runner.model_config,
                "judge": self.judge_runner.model_config,
                "evaluation": eval_config,
            },
            "total": len(results),
            "passed": passed_count,
            "partial": partial_count,
            "failed": failed_count,
            "avg_score": avg_score,
        }

        save_json(summary_path, summary)
