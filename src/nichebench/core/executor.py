"""Clean, elegant test execution engine for NicheBench.

This module provides a unified test execution system with no code duplication
and clear separation of concerns.
"""

import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import yaml
from deepeval.test_case import LLMTestCase

from nichebench.config.nichebench_config import get_config
from nichebench.config.settings import settings
from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.profiles import resolve_profile
from nichebench.core.prompt_loader import load_prompt_text
from nichebench.core.scoring import CheckResult, RuntimeScorer
from nichebench.core.validation import ValidationError, validate_runtime_testcase
from nichebench.core.workspace import Workspace
from nichebench.metrics.bug_fixing_metric import DeepEvalBugFixingMetric
from nichebench.metrics.code_generation_metric import DeepEvalCodeGenerationMetric
from nichebench.metrics.deepeval_quiz_metric import DeepEvalQuizMetric
from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import LiteLLMJudge
from nichebench.providers.mut_prompt_composer import MUTPromptComposer
from nichebench.utils.git import find_git_root, resolve_branch_to_sha
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
        self.runtime_artifacts: Dict[str, Any] = {}
        self.effective_profile: Optional[str] = None
        self.resolved_flags: Dict[str, bool] = {}
        self.trial: int = 1
        self.trials_total: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = {
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

        d["trial"] = self.trial
        d["trials_total"] = self.trials_total

        if isinstance(self.judge_output, dict):
            d["deterministic_score"] = self.judge_output.get("deterministic_score")
            d["judge_score"] = self.judge_output.get("judge_score")
            d["final_score"] = self.judge_output.get("hybrid_score")

        # Task 2.2: Persist base_branch and resolved_sha
        if hasattr(self.test_case, "base_branch") and self.test_case.base_branch:
            d["base_branch"] = self.test_case.base_branch
        if hasattr(self.test_case, "resolved_sha") and self.test_case.resolved_sha:
            d["resolved_sha"] = self.test_case.resolved_sha

        # Task 3.4 & 5.3: Runtime artifacts metadata (keys only — raw payload lives on disk)
        if hasattr(self, "runtime_artifacts") and self.runtime_artifacts:
            d["artifact_keys"] = list(self.runtime_artifacts.keys())
        if hasattr(self, "effective_profile"):
            d["effective_profile"] = getattr(self, "effective_profile")
        if hasattr(self, "resolved_flags"):
            d["resolved_flags"] = getattr(self, "resolved_flags")

        return d


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
        elif category == "code_generation":
            return self._run_single_turn(test_case, system_prompt, category)
        elif category in ("code_agent", "bug_fixing"):
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
        """Run multi-turn conversation (bug fixing) or agentic execution (code_agent)."""
        # Start conversation
        if category == "code_agent":
            # Use LangGraph agent for proper plan-based execution
            from nichebench.frameworks.drupal.prompts.CODE_AGENT import (
                CODE_AGENT_BASE_PROMPT,
            )
            from nichebench.providers.langgraph_code_agent import LangGraphCodeAgent

            # Create the LangGraph agent with correct parameters
            agent = LangGraphCodeAgent(
                model=self.model_str,
                custom_llm_params=self.model_config.get("parameters", {}),
            )

            # Prepare context for the agent
            context = getattr(test_case, "context", None) or test_case.raw.get("context", "")
            task_description = getattr(test_case, "prompt", "") or test_case.raw.get("prompt", "")

            # Create progress callback from runner
            progress_callback = None
            if runner:

                def update_progress(message: str, step: int):
                    runner.update_test_status(f"[yellow]🧪 {test_case.id}[/yellow] - {message}", step)

                progress_callback = update_progress

            # Execute the task - returns string result
            final_output = agent.execute_task(
                task_description=task_description, context=context, progress_callback=progress_callback
            )

            # CRITICAL: Reset global litellm.api_base after LangGraph agent finishes
            # The LangGraph agent uses ChatLiteLLM which sets global state that affects
            # subsequent judge calls. We need to clear this to prevent 404 errors.
            try:
                import litellm

                if hasattr(litellm, "api_base"):
                    setattr(litellm, "api_base", None)
            except ImportError:
                pass  # litellm not available, ignore

            # Build comprehensive input message showing the full prompt chain
            input_parts = [
                f"TASK: {task_description}",
            ]
            if context:
                input_parts.append(f"CONTEXT: {context}")

            input_parts.extend(
                [
                    f"\nSYSTEM PROMPT: {(system_prompt or CODE_AGENT_BASE_PROMPT or '')[:200]}...",
                    "\nEXECUTION: LangGraph plan-based code generation",
                ]
            )

            initial_user_message = "\n".join(input_parts)

            return final_output, initial_user_message

        elif category == "bug_fixing":
            conversation = MUTPromptComposer.start_bug_conversation(test_case, system_prompt)
        else:
            raise ValueError(f"Multi-turn not supported for category: {category}")

        # Execute conversation turns
        messages: Optional[List[Dict[str, str]]] = conversation._format_for_llm()
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
        first_user_message: Optional[str] = None
        for turn in conversation.turns:
            if turn.role == "user":
                first_user_message = turn.content
                break

        return final_output, first_user_message or "Multi-turn conversation"


def _build_runtime_task_description(raw: Dict[str, Any]) -> str:
    """Build a concise task description string from a manifest raw dict."""
    parts: list[str] = []
    title = str(raw.get("title", "")).strip()
    if title:
        parts.append(f"Task: {title}")
    desc = raw.get("description_structured", {})
    if isinstance(desc, dict):
        bg = str(desc.get("background", "")).strip()
        if bg:
            parts.append(f"Background:\n{bg}")
        ac = desc.get("acceptance_criteria", [])
        if ac and isinstance(ac, list):
            parts.append("Acceptance Criteria:\n" + "\n".join(f"- {c}" for c in ac))
    return "\n\n".join(parts) if parts else "(no task description available)"


def _build_runtime_artifact_summary(
    artifacts: Dict[str, Any],
    max_diff_chars: int = 8000,
    max_log_chars: int = 4000,
) -> str:
    """Build a concise artifact summary string for the LLM judge."""
    parts: list[str] = []

    diff = artifacts.get("final.diff", "")
    if diff and isinstance(diff, str):
        if len(diff) > max_diff_chars:
            diff = diff[:max_diff_chars] + f"\n... [truncated at {max_diff_chars} chars]"
        parts.append(f"=== GIT DIFF (final.diff) ===\n{diff}")

    log = artifacts.get("run.log", "")
    if log and isinstance(log, str):
        if len(log) > max_log_chars:
            log = log[-max_log_chars:] + f"\n... [showing last {max_log_chars} chars]"
        parts.append(f"=== AGENT RUN LOG (run.log) ===\n{log}")

    checks = artifacts.get("checks.json", {})
    if checks and isinstance(checks, dict):
        det = checks.get("deterministic", [])
        if det and isinstance(det, list):
            lines = [
                f"  [{'PASS' if c.get('passed') else 'FAIL'}] " f"{c.get('name', '')}: {c.get('message', '')}"
                for c in det
            ]
            parts.append("=== DETERMINISTIC CHECKS ===\n" + "\n".join(lines))

    phpcs = artifacts.get("phpcs.json", {})
    if phpcs and isinstance(phpcs, dict):
        totals = phpcs.get("totals", {})
        parts.append(
            f"=== PHPCS ===\n"
            f"errors={totals.get('errors', '?')}, "
            f"warnings={totals.get('warnings', '?')}, "
            f"fixable={totals.get('fixable', '?')}"
        )

    phpstan = artifacts.get("phpstan.json", {})
    if phpstan and isinstance(phpstan, dict):
        totals = phpstan.get("totals", {})
        parts.append(
            f"=== PHPSTAN ===\n" f"error={totals.get('error', '?')}, " f"maybe_error={totals.get('maybe_error', '?')}"
        )

    return "\n\n".join(parts) if parts else "(no artifacts available)"


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
        runtime_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Evaluate MUT output using judge.

        Args:
            test_case: Test case specification
            category: Test category (quiz, code_generation, bug_fixing, runtime)
            user_input: User input/prompt
            mut_output: Model output to evaluate
            judge_system_prompt: Optional system prompt for judge
            runtime_artifacts: Optional dict of runtime artifacts for category='runtime'

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

        # Attach additional context for better judge evaluation
        if hasattr(test_case, "context") and test_case.context:
            setattr(stc, "context", test_case.context)

        if hasattr(test_case, "summary") and test_case.summary:
            setattr(stc, "summary", test_case.summary)

        # Attach judge notes if available in test case data
        if test_case.raw.get("judge_notes"):
            setattr(stc, "judge_notes", test_case.raw["judge_notes"])

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

        elif category == "code_agent":
            # Use the same metric as code_generation since we're testing the same capabilities
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

        elif category == "runtime":
            # Runtime tasks use score_runtime with artifacts
            llm_judge_config = test_case.raw.get("llm_judge", {})
            checklist = llm_judge_config.get("checklist", [])

            if not checklist:
                # No checklist means perfect score by default
                judge_output = {"overall_score": 1.0, "criteria": [], "summary": "No checklist - passing by default"}
                passed = True
            else:
                # Call score_runtime with artifacts
                if runtime_artifacts:
                    task_desc = _build_runtime_task_description(test_case.raw)
                    artifact_summary = _build_runtime_artifact_summary(runtime_artifacts)

                    # Set the input for the judge
                    setattr(stc, "input", task_desc)
                    setattr(stc, "actual_output", artifact_summary)

                judge_output = self.judge.score_runtime(
                    task_description=str(getattr(stc, "input", user_input)),
                    artifact_summary=str(getattr(stc, "actual_output", mut_output)),
                    checklist_items=checklist,
                    model=self.model_str,
                    model_params=self.model_config.get("parameters", {}),
                    system_prompt=llm_judge_config.get("model_role", "Be a fair evaluator."),
                )

                overall_score = float(judge_output.get("overall_score", 0.0))
                passed = overall_score >= 0.5  # Default threshold

        else:
            # Fallback for unknown categories - never random (test requirement)
            judge_output = {
                "overall_score": 1.0,
                "explanation": "Unknown category - passing by default",
                "judge_system_prompt": bool(judge_system_prompt),
            }
            passed = True

        return judge_output, passed


class TestExecutor:
    """Main test execution orchestrator with parallel execution support."""

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
        self.framework = framework
        self.category = category
        self.parallelism = parallelism
        # Tracks whether --model was explicitly passed via CLI; used to
        # suppress runtime_opencode_model config override so the CLI flag wins.
        self._cli_model_override = cli_model_override
        self.config = get_config()
        self.evaluation_config = self.config.get_evaluation_config()

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

        # Thread-safe lock for progress updates
        self._progress_lock = Lock()
        # Results output directory for artifact persistence
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

    def _run_runtime_preflight_host(
        self,
        runtime_config: Dict[str, Any],
        runtime_mode: str,
    ) -> None:
        """Run host-side preflight checks for runtime execution.

        Args:
            runtime_config: Runtime configuration dict
            runtime_mode: Effective runtime mode (cage/host)

        Raises:
            ValidationError: If preflight checks fail
        """
        # Normalize mode (container -> cage)
        effective_mode = "cage" if runtime_mode in ("cage", "container") else runtime_mode

        # Cage mode preflight
        if effective_mode == "cage":
            image = str(runtime_config.get("runtime_container_image", "")).strip()

            # Check for empty image
            if not image:
                raise ValidationError("runtime_container_image must be configured")

            # Check for floating tag (:latest, :<no tag>)
            if ":latest" in image:
                raise ValidationError("floating tag :latest is not allowed - use a pinned image reference")
            if "/" not in image or ":" not in image:
                # Missing repo or missing tag
                raise ValidationError("Container image must be a pinned reference (not :latest or untagged)")

            # Docker/ddev preflight (best-effort)
            try:
                subprocess.run(["docker", "--version"], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                # May fail in CI/test environments - don't block preflight
                pass

        # Host mode preflight (minimal)
        elif effective_mode == "host":
            # Host mode requires minimal setup
            try:
                subprocess.run(["ddev", "--version"], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                # May fail if ddev not installed - don't block preflight
                pass

    def _run_runtime_preflight_workspace(
        self,
        workspace_path: Path,
        runtime_mode: str,
    ) -> None:
        """Run workspace-side runtime preflight checks.

        Exists as a distinct hook for runtime/cage test coverage.
        """
        del runtime_mode
        if not workspace_path.exists():
            raise ValidationError(f"Workspace path does not exist: {workspace_path}")

    def _inject_task_markdown(self, workspace_path: Path, test_case: TestCaseSpec) -> None:
        """Inject TASK.md into workspace when present on the testcase."""
        task_markdown = str(test_case.raw.get("task_markdown", "")).strip()
        if not task_markdown:
            return
        (workspace_path / "TASK.md").write_text(task_markdown, encoding="utf-8")

    def _load_runtime_checks(self, test_case: TestCaseSpec) -> List[Dict[str, Any]]:
        """Load and normalize runtime checks from testcase raw config."""
        raw_checks = test_case.raw.get("checks", [])
        if not isinstance(raw_checks, dict):
            return RuntimeScorer.normalize_checks(raw_checks)

        checks_path = self._resolve_runtime_checks_file(test_case)
        if checks_path is None:
            return RuntimeScorer.normalize_checks(raw_checks)

        checks_by_id = self._load_runtime_checks_by_id(checks_path)
        if not checks_by_id:
            return RuntimeScorer.normalize_checks(raw_checks)

        normalized: List[Dict[str, Any]] = []
        critical_categories = {"fail_to_pass", "pass_to_pass", "static"}

        for category in ("fail_to_pass", "pass_to_pass", "static"):
            entries = raw_checks.get(category, [])
            if not isinstance(entries, list):
                continue
            for item in entries:
                item_text = str(item)
                resolved = checks_by_id.get(item_text)
                if isinstance(resolved, dict):
                    concrete = dict(resolved)
                    concrete.setdefault("id", item_text)
                    concrete.setdefault("category", category)
                    concrete.setdefault("critical", category in critical_categories)
                    normalized.append(concrete)
                    continue

                if self._looks_like_shell_command(item_text):
                    normalized.append(
                        {
                            "name": item_text,
                            "type": category,
                            "command": item_text,
                            "critical": category in critical_categories,
                        }
                    )
                    continue

                normalized.append(
                    {
                        "name": item_text,
                        "type": "unknown_runtime_check_id",
                        "id": item_text,
                        "category": category,
                        "critical": category in critical_categories,
                        "message": f"Unknown runtime check id: {item_text}",
                    }
                )

        for cmd in raw_checks.get("required_commands", []):
            normalized.append({"name": str(cmd), "type": "required_command", "command": str(cmd), "critical": True})
        if raw_checks.get("allowed_paths"):
            normalized.append(
                {
                    "name": "path_policy",
                    "type": "path_policy",
                    "allowed_paths": raw_checks.get("allowed_paths", []),
                    "critical": True,
                }
            )

        return normalized

    @staticmethod
    def _looks_like_shell_command(value: str) -> bool:
        return any(ch.isspace() for ch in value)

    @staticmethod
    def _resolve_runtime_checks_file(test_case: TestCaseSpec) -> Optional[Path]:
        if not test_case.file_path:
            return None
        manifest_path = Path(test_case.file_path)
        if manifest_path.parent.name != "manifest":
            return None
        if manifest_path.parent.parent.name != "tasks":
            return None
        checks_path = manifest_path.parent.parent / "checks" / manifest_path.name
        if not checks_path.exists():
            return None
        return checks_path

    @staticmethod
    def _load_runtime_checks_by_id(checks_path: Path) -> Dict[str, Dict[str, Any]]:
        try:
            parsed = yaml.safe_load(checks_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        if not isinstance(parsed, dict):
            return {}
        checks = parsed.get("checks")
        if not isinstance(checks, list):
            return {}

        by_id: Dict[str, Dict[str, Any]] = {}
        for check in checks:
            if not isinstance(check, dict):
                continue
            check_id = check.get("id")
            if check_id is None:
                continue
            by_id[str(check_id)] = check
        return by_id

    def _build_runtime_metadata(
        self,
        test_case: TestCaseSpec,
        profile: Any,
        runtime_mode: str,
        runtime_config: Dict[str, Any],
        workspace: Any,
        island_topology: Optional[Dict[str, Any]] = None,
        effective_image: Optional[str] = None,
        retry_info: Optional[Dict[str, Any]] = None,
        review_pass_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build runtime metadata dict.

        Args:
            test_case: Test case specification
            profile: Profile object
            runtime_mode: Runtime mode (cage/host)
            runtime_config: Runtime configuration
            workspace: Workspace object
            island_topology: Optional island topology mapping
            effective_image: Optional effective image (after DDEV resolution)
            retry_info: Optional retry info dict
            review_pass_info: Optional review pass info dict for two-pass nudge flow

        Returns:
            Metadata dict with runtime information
        """
        # Normalize mode
        effective_runtime_mode = "cage" if runtime_mode in ("cage", "container") else runtime_mode

        # Base and effective image fields
        base_image = str(runtime_config.get("runtime_container_image", ""))
        metadata: Dict[str, Any] = {
            "effective_runtime_mode": effective_runtime_mode,
            "runtime_mode": runtime_mode,
            "runtime_container_image_base": base_image,
            "runtime_container_image_effective": effective_image or base_image,
        }

        # MUT model binding
        mut_provider = str(self.mut_runner.model_config.get("provider", ""))
        mut_model = str(self.mut_runner.model_config.get("model", ""))
        metadata["mut_model_binding"] = f"{mut_provider}/{mut_model}"

        # OpenCode model binding used in cage runtime execution
        opencode_provider, opencode_model_id = self._compute_opencode_model_binding(
            mut_provider,
            mut_model,
            runtime_config,
            cli_model_override=self._cli_model_override,
        )
        metadata["opencode_provider"] = opencode_provider
        metadata["opencode_model_id"] = opencode_model_id
        metadata["opencode_model_binding"] = f"{opencode_provider}/{opencode_model_id}"

        # Tool flags from profile
        metadata["tool_flags"] = {
            "allow_web_search": getattr(profile, "allow_web_search", False),
            "allow_browser": getattr(profile, "allow_browser", False),
            "allow_mcp": getattr(profile, "allow_mcp", True),
            "allow_external_network_for_shell": getattr(profile, "allow_external_network_for_shell", False),
        }

        # Island topology if provided
        if island_topology:
            metadata["island_topology"] = island_topology

        # Retry info — only present when retry was actually attempted (not None/False)
        if retry_info:
            metadata["retry_info"] = retry_info

        # Review pass info — only present when two-pass review nudge was attempted
        if review_pass_info:
            metadata["review_pass_info"] = review_pass_info

        return metadata

    def _resolve_effective_cage_image(
        self,
        runtime_config: Dict[str, Any],
    ) -> str:
        """Resolve effective cage image, handling DDEV capability checks and auto-build.

        Args:
            runtime_config: Runtime configuration dict

        Returns:
            Effective image tag to use

        Raises:
            ValidationError: If image resolution fails
        """
        enable_ddev = bool(runtime_config.get("runtime_container_enable_ddev", True))
        base_image = str(runtime_config.get("runtime_container_image", ""))
        ddev_image = str(runtime_config.get("runtime_container_ddev_image", "nichebench/opencode-ddev:1.14.25"))
        auto_build = bool(runtime_config.get("runtime_container_ddev_auto_build", True))

        # If DDEV disabled, return base image as-is
        if not enable_ddev:
            return base_image

        # Probe base image for ddev/docker binaries
        has_ddev = self._probe_image_for_ddev(base_image)

        if has_ddev:
            # Base image already has DDEV
            return base_image

        # Missing DDEV binaries
        if auto_build:
            # Build derived image
            self._build_ddev_image(base_image, ddev_image)
            # Verify derived image
            if self._probe_image_for_ddev(ddev_image):
                return ddev_image
            raise ValidationError(
                f"Derived DDEV image {ddev_image} still lacks required ddev/docker/git binaries or ddev drush support"
            )
        else:
            raise ValidationError(
                "Base image "
                f"{base_image} lacks required ddev/docker/git binaries or ddev drush support "
                "and auto_build is disabled"
            )

    def _probe_image_for_ddev(self, image: str) -> bool:
        """Probe image for ddev/docker/git and `ddev drush` command availability.

        Args:
            image: Image tag to probe

        Returns:
            True if required tooling exists, False otherwise
        """
        try:
            # Use shell probe to ensure binaries and ddev drush command parity.
            cmd = [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                image,
                "-c",
                (
                    "command -v ddev && command -v docker && command -v git && "
                    "ddev drush --help >/tmp/ddev-drush.out 2>&1 || true; "
                    "! grep -qi 'unknown command \"drush\"' /tmp/ddev-drush.out"
                ),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception:
            return False

    def _build_ddev_image(self, base_image: str, ddev_image: str) -> None:
        """Build DDEV-capable derived image.

        Args:
            base_image: Base image tag
            ddev_image: Derived image tag to build

        Raises:
            ValidationError: If build fails
        """
        dockerfile_path = Path(__file__).resolve().parents[3] / "docker" / "opencode-ddev" / "Dockerfile"
        try:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    ddev_image,
                    "-f",
                    str(dockerfile_path),
                    "--build-arg",
                    f"BASE_IMAGE={base_image}",
                    str(dockerfile_path.parent),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=300,
            )
        except subprocess.CalledProcessError as e:
            raise ValidationError(f"Failed to build ddev-capable image: {e.stderr}")

    def execute_test(self, test_case: TestCaseSpec, runner=None, trial: int = 0) -> TestResult:
        """Execute a single test case."""
        if self.category == "runtime":
            if runner:
                runner.update_test_status(
                    f"[yellow]🧪 {test_case.id}[/yellow] - Running runtime orchestration (trial {trial + 1})...", 1
                )
            return self.execute_runtime_test(test_case, trial=trial)

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

    def execute_tests_parallel(
        self, test_cases: List[TestCaseSpec], runner=None, save_callback=None, summary_callback=None, trials: int = 1
    ) -> List[TestResult]:
        """Execute multiple test cases with parallel support."""
        if self.parallelism == 1:
            # Sequential execution - use the original flow for compatibility
            sequential_results: List[TestResult] = []
            for trial_num in range(trials):
                for test_case in test_cases:
                    if runner:
                        runner.start_test(test_case.id)

                    result = self.execute_test(test_case, runner, trial=trial_num)
                    result.trial = trial_num + 1  # 1-based trial index
                    result.trials_total = trials
                    sequential_results.append(result)

                    # Save incrementally in sequential mode
                    if save_callback:
                        save_callback(result)
                    if summary_callback:
                        summary_callback(sequential_results)

                    if runner:
                        runner.finish_test(test_case.id, result.passed, result.error)

            return sequential_results

        # Parallel execution
        parallel_results: List[Optional[TestResult]] = [None] * len(test_cases)  # Pre-allocate to maintain order
        completed_results: List[TestResult] = []  # For incremental callbacks

        def execute_with_index(index_and_test):
            index, test_case = index_and_test
            # Create a thread-safe progress callback
            safe_runner = self._create_thread_safe_runner(runner, index, len(test_cases))
            return index, self.execute_test(test_case, safe_runner, trial=0)  # trials loop handled in sequential path

        with ThreadPoolExecutor(max_workers=self.parallelism) as executor:
            # Submit all tasks
            future_to_index = {
                executor.submit(execute_with_index, (i, test_case)): i for i, test_case in enumerate(test_cases)
            }

            # Collect results as they complete
            for future in as_completed(future_to_index):
                index, result = future.result()
                parallel_results[index] = result
                completed_results.append(result)

                # Save result immediately as it completes (thread-safe)
                if save_callback:
                    with self._progress_lock:
                        save_callback(result)

                # Update summary with current completed results
                if summary_callback:
                    with self._progress_lock:
                        summary_callback(completed_results)

                # Thread-safe progress update
                if runner:
                    with self._progress_lock:
                        worker_id = index % self.parallelism
                        runner.finish_worker_test(worker_id, result.test_case.id, result.passed)
                        runner.advance_progress(1)

                        # Update main progress description
                        runner.progress.update(
                            runner.main_task,
                            description=f"[cyan]Running {runner.framework}/{runner.category}[/cyan] - "
                            f"✅ {runner.passed_tests} passed, ❌ {runner.failed_tests} failed",
                        )

        # Filter out None values and return
        final_results: List[TestResult] = [r for r in parallel_results if r is not None]
        return final_results

    def _create_thread_safe_runner(self, runner, test_index: int, total_tests: int):
        """Create a thread-safe wrapper for progress updates."""
        if not runner:
            return None

        # Use test_index as worker_id for parallel display
        worker_id = test_index % self.parallelism

        class ThreadSafeRunner:
            def __init__(self, original_runner, lock, worker_id, test_index, total_tests):
                self._original = original_runner
                self._lock = lock
                self._worker_id = worker_id
                self._test_index = test_index
                self._total = total_tests

            def update_test_status(self, message: str, step: int):
                with self._lock:
                    # Extract test ID from message
                    test_id = "test"
                    if "🧪" in message:
                        # Extract test ID from format "[yellow]🧪 {test_id}[/yellow] - ..."
                        parts = message.split("🧪")
                        if len(parts) > 1:
                            test_part = parts[1].split("[/yellow]")[0].strip()
                            test_id = test_part

                    # Convert message to worker status
                    if "Running MUT" in message:
                        status = "Running MUT"
                    elif "Running Judge" in message:
                        status = "Running Judge"
                    else:
                        status = "Processing"

                    self._original.update_worker_status(self._worker_id, test_id, status, step)

            def advance_progress(self, amount: int):
                # Don't call this directly in threaded context
                pass

        return ThreadSafeRunner(runner, self._progress_lock, worker_id, test_index, total_tests)

    def setup_results_directory(self, results_config: Dict[str, Any]) -> Tuple[Path, Path, Path]:
        """Setup results directory and return paths."""
        timestamp = datetime.now().strftime(results_config["timestamp_format"])
        outdir = Path("results") / self.framework / self.category / self.mut_model_str.replace("/", "-") / timestamp
        ensure_results_dir(outdir)
        self.results_outdir = outdir

        details_path = outdir / "details.jsonl"
        summary_path = outdir / "summary.json"

        return details_path, summary_path, outdir

    def save_incremental_result(self, result: TestResult, details_path: Path):
        """Save a single result incrementally."""
        save_jsonl(details_path, [result.to_dict()], mode="a")
        self._save_runtime_artifacts(result)

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
            if self.category in ("code_generation", "bug_fixing", "code_agent"):
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

    def _run_container_runtime_task_with_retry(
        self,
        test_case: TestCaseSpec,
        workspace: Any,
        agent_manifest: Dict[str, Any],
        runtime_config: Dict[str, Any],
        profile: Any,
        timeout_seconds: int,
        task_input_override: Optional[str] = None,
    ) -> Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Run cage task with one-step auto-retry for invalid_request_error due to unknown tool.

        When the first run fails with a rejected tool attempt (e.g., 'exec' not in
        request.tools), retries once with an appended instruction to use 'bash' for
        shell commands and continue.

        Returns:
            Tuple of (mut_output, user_input, run_log, island_topology, effective_image,
                     trajectory, retry_info)
            trajectory is None if capture fails (best-effort).
            retry_info is None if no retry occurred, otherwise {"attempted": True, "reason": str}
        """
        # Fast path: attempt once without retry
        (
            mut_output,
            user_input,
            run_log,
            island_topology,
            effective_image,
            trajectory,
        ) = self._run_container_runtime_task(
            test_case=test_case,
            workspace=workspace,
            agent_manifest=agent_manifest,
            runtime_config=runtime_config,
            profile=profile,
            timeout_seconds=timeout_seconds,
            task_input_override=task_input_override,
        )

        # Retry on invalid tool call/schema/JSON-call issues.
        retry_info: Optional[Dict[str, Any]] = None
        retry_attempts = 0
        max_retry_attempts = max(int(runtime_config.get("runtime_tool_retry_attempts", 3)), 0)

        while retry_attempts < max_retry_attempts:
            if not run_log or "invalid_request_error" not in run_log.lower():
                break

            rejected = self._parse_rejected_tool_attempts(run_log)
            json_parse_in_log = "Failed to parse tool call arguments as JSON" in run_log
            if not (rejected or json_parse_in_log):
                break

            is_json_parse_class = False
            json_parse_tool_names = {"json", "parse", "arguments", "tool_call"}
            if rejected:
                for r in rejected:
                    tn = r.get("tool_name", "")
                    if tn and any(pattern in tn for pattern in json_parse_tool_names):
                        is_json_parse_class = True
                        break
            elif json_parse_in_log:
                is_json_parse_class = True

            if is_json_parse_class:
                retry_trigger_reason = "json_parse_failure"
            else:
                retry_trigger_reason = f"rejected tool attempts: {[r['tool_name'] for r in rejected]}"
                retry_appendix = (
                    " IMPORTANT: You attempted a tool that is not in the allowed list or "
                    "called it with wrong parameters. Use exact tool names: read, write, edit, bash. "
                    "IMPORTANT: When calling 'read', you MUST provide the 'filePath' parameter "
                    "(and optionally 'offset' and 'limit'). "
                    "Do not call any tool not in {read, write, edit, bash}. Continue the task."
                )
                if task_input_override is not None:
                    # task_input_override drives the agent prompt instead of TASK.md;
                    # append here so the retry sees the correction.
                    task_input_override = task_input_override.rstrip() + retry_appendix
                else:
                    workspace_host_path = (
                        Path(workspace.path).resolve() if hasattr(workspace, "path") else Path(workspace.path).resolve()
                    )
                    task_md_path = workspace_host_path / "TASK.md"
                    try:
                        existing_task_md = task_md_path.read_text(encoding="utf-8").strip()
                        task_md_path.write_text(existing_task_md + retry_appendix, encoding="utf-8")
                    except OSError:
                        pass  # If we can't update TASK.md, proceed with retry anyway

            retry_attempts += 1
            retry_info = {
                "attempted": True,
                "reason": retry_trigger_reason,
                "count": retry_attempts,
            }

            (
                mut_output_retry,
                user_input_retry,
                run_log_retry,
                island_topology_retry,
                effective_image_retry,
                trajectory_retry,
            ) = self._run_container_runtime_task(
                test_case=test_case,
                workspace=workspace,
                agent_manifest=agent_manifest,
                runtime_config=runtime_config,
                profile=profile,
                timeout_seconds=timeout_seconds,
                task_input_override=task_input_override,
            )
            mut_output = mut_output_retry
            user_input = user_input_retry
            run_log = run_log_retry
            trajectory = trajectory_retry

        return mut_output, user_input, run_log, island_topology, effective_image, trajectory, retry_info

    def _run_container_runtime_task(
        self,
        test_case: TestCaseSpec,
        workspace: Any,
        agent_manifest: Dict[str, Any],
        runtime_config: Dict[str, Any],
        profile,
        timeout_seconds: int,
        task_input_override: Optional[str] = None,
    ) -> Tuple[str, str, str, Dict[str, Any], str, Optional[Dict[str, Any]]]:
        """Run OpenCode inside a dedicated container with docker socket access.

        The socket bind remains highly privileged even with user/capability hardening,
        so these defaults reduce but do not eliminate host-level risk.

        Returns:
            Tuple of (mut_output, user_input, run_log, island_topology, effective_image, trajectory)
            trajectory is None if capture fails (best-effort).
        """
        if isinstance(workspace, Workspace):
            workspace_host_path = Path(workspace.path).resolve()
        else:
            workspace_host_path = Path(workspace.path).resolve()

        # Use override if provided (e.g., review nudge for second pass), otherwise read from TASK.md
        if task_input_override is not None:
            task_input = task_input_override
        else:
            prompt = getattr(test_case, "prompt", "") or test_case.raw.get("prompt", "") or ""
            context = getattr(test_case, "context", "") or test_case.raw.get("context", "") or ""
            task_input = prompt if not context else f"{prompt}\n\nContext:\n{context}"
            task_markdown_path = workspace_host_path / "TASK.md"
            try:
                task_markdown = task_markdown_path.read_text(encoding="utf-8").strip()
                if task_markdown:
                    task_input = task_markdown
            except OSError:
                pass

        workspace_container_path = "/workspace"
        input_island_host = workspace_host_path
        input_island_container = "/nichebench/islands/input"
        prompt_file_host = input_island_host / ".nichebench-runtime-task.txt"
        prompt_file_container = Path(workspace_container_path) / ".nichebench-runtime-task.txt"
        prompt_file_host.write_text(task_input, encoding="utf-8")

        env = {
            "NB_TASK_ID": test_case.id,
            "NB_TOOL_PROFILE": profile.name,
            "NB_MODEL_PROVIDER": str(self.mut_runner.model_config.get("provider", "")),
            "NB_MODEL_NAME": str(self.mut_runner.model_config.get("model", "")),
            "NB_TASK_PROMPT_FILE": str(prompt_file_container),
            "NB_RUNTIME_MODE": "cage",
        }

        mut_provider = str(self.mut_runner.model_config.get("provider", "")).strip()
        mut_model_name = str(self.mut_runner.model_config.get("model", "")).strip()
        if not mut_provider or not mut_model_name:
            raise ValidationError("Cage mode requires explicit MUT provider/model binding")

        # Compute OpenCode model binding with normalization
        opencode_provider, opencode_model_id = self._compute_opencode_model_binding(
            mut_provider,
            mut_model_name,
            runtime_config,
            cli_model_override=self._cli_model_override,
        )
        opencode_model_binding = f"{opencode_provider}/{opencode_model_id}"

        # Get provider API keys from host environment
        api_keys = self._get_provider_api_keys(opencode_provider)

        self._write_cage_opencode_json(
            workspace_host_path=workspace_host_path,
            opencode_provider=opencode_provider,
            opencode_model_id=opencode_model_id,
        )

        # Run-scoped OpenCode state roots prevent any inheritance from host user
        # state and stay outside of task workspace to avoid diff pollution.
        state_root_tmp = tempfile.mkdtemp(prefix="nichebench-cage-state-")
        state_root = Path(state_root_tmp)
        home_host = state_root / "home"
        xdg_config_host = state_root / "xdg-config"
        xdg_data_host = state_root / "xdg-data"
        xdg_state_host = state_root / "xdg-state"
        xdg_cache_host = state_root / "xdg-cache"
        for path in (home_host, xdg_config_host, xdg_data_host, xdg_state_host, xdg_cache_host):
            path.mkdir(parents=True, exist_ok=True)

        output_island_host = Path(
            getattr(workspace, "run_artifacts_path", workspace_host_path / "results" / "run")
        ).resolve()
        output_island_host.mkdir(parents=True, exist_ok=True)
        output_trace_island_container = "/nichebench/islands/output-trace"
        trace_host_path = output_island_host / "trace"
        trace_host_path.mkdir(parents=True, exist_ok=True)
        trace_container_path = f"{output_trace_island_container}/trace"

        island_topology: Dict[str, Any] = {
            "workspace": {
                "host_path": str(workspace_host_path),
                "container_path": workspace_container_path,
            },
            "input_island": {
                "host_path": str(input_island_host),
                "container_path": input_island_container,
            },
            "output_trace_island": {
                "host_path": str(output_island_host),
                "container_path": output_trace_island_container,
                "trace_host_path": str(trace_host_path),
                "trace_container_path": trace_container_path,
            },
        }

        env["NB_ISLAND_INPUT"] = input_island_container
        env["NB_ISLAND_OUTPUT_TRACE"] = output_trace_island_container
        env["NB_ISLAND_OUTPUT"] = output_trace_island_container
        env["NB_ISLAND_TRACE"] = trace_container_path

        ops_island_host_path = runtime_config.get("runtime_ops_island_host_path")
        enable_ops_island = bool(runtime_config.get("runtime_enable_ops_island", False) or ops_island_host_path)
        if enable_ops_island:
            ops_island_host = Path(str(ops_island_host_path)) if ops_island_host_path else (state_root / "ops-island")
            ops_island_host.mkdir(parents=True, exist_ok=True)
            ops_island_container = "/nichebench/islands/ops"
            island_topology["ops_island"] = {
                "host_path": str(ops_island_host),
                "container_path": ops_island_container,
            }
            env["NB_ISLAND_OPS"] = ops_island_container

        container_state_root = "/nichebench/state"
        env["HOME"] = f"{container_state_root}/home"
        env["XDG_CONFIG_HOME"] = f"{container_state_root}/xdg-config"
        env["XDG_DATA_HOME"] = f"{container_state_root}/xdg-data"
        env["XDG_STATE_HOME"] = f"{container_state_root}/xdg-state"
        env["XDG_CACHE_HOME"] = f"{container_state_root}/xdg-cache"

        # Resolve effective cage image (handles DDEV capability checks and auto-build)
        image = self._resolve_effective_cage_image(runtime_config)
        runtime_user = str(runtime_config.get("runtime_container_user", "1000:1000"))
        read_only = bool(runtime_config.get("runtime_container_read_only", False))
        command = [
            "docker",
            "run",
            "--rm",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            runtime_user,
        ]
        # Add docker socket group access for non-root user (best effort)
        try:
            docker_socket_gid = os.stat("/var/run/docker.sock").st_gid
            command.extend(["--group-add", str(docker_socket_gid)])
        except Exception:
            # If stat fails, continue without group-add (socket may not exist or we lack permissions)
            pass
        command.extend(
            [
                "-v",
                f"{workspace_host_path}:{workspace_container_path}",
                "-v",
                f"{input_island_host}:{input_island_container}:ro",
                "-v",
                f"{output_island_host}:{output_trace_island_container}",
                "-v",
                "/var/run/docker.sock:/var/run/docker.sock",
                "-w",
                workspace_container_path,
                "-v",
                f"{home_host}:{container_state_root}/home",
                "-v",
                f"{xdg_config_host}:{container_state_root}/xdg-config",
                "-v",
                f"{xdg_data_host}:{container_state_root}/xdg-data",
                "-v",
                f"{xdg_state_host}:{container_state_root}/xdg-state",
                "-v",
                f"{xdg_cache_host}:{container_state_root}/xdg-cache",
            ]
        )
        if "ops_island" in island_topology:
            command.extend(
                [
                    "-v",
                    (
                        f"{island_topology['ops_island']['host_path']}"
                        f":{island_topology['ops_island']['container_path']}"
                    ),
                ]
            )
        if read_only:
            command.extend(["--read-only", "--tmpfs", "/tmp", "--tmpfs", "/run"])
        for key, value in env.items():
            command.extend(["-e", f"{key}={value}"])
        # Add provider API keys from host environment
        for key, value in api_keys.items():
            command.extend(["-e", f"{key}={value}"])
        command.extend(
            [
                "--entrypoint",
                "opencode",
                image,
                "run",
                "--pure",
                "--dangerously-skip-permissions",
                "--model",
                opencode_model_binding,
                task_input,
            ]
        )

        try:
            run_start = datetime.now(tz=timezone.utc)
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            run_end = datetime.now(tz=timezone.utc)
            run_log = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}".strip()
            (output_island_host / "run.log").write_text(run_log, encoding="utf-8")
            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.strip() or f"Container OpenCode command failed with exit {result.returncode}"
                )

            # Best-effort trajectory capture from cage state SQLite
            trajectory: Optional[Dict[str, Any]] = None
            try:
                db_path = xdg_data_host / "opencode" / "opencode.db"
                system_prompt = self._read_workspace_system_prompt(workspace_host_path)
                trajectory = self._build_trajectory_from_sqlite(
                    db_path=db_path,
                    test_case_id=test_case.id,
                    model_str=self.mut_model_str,
                    start_time=run_start,
                    end_time=run_end,
                    system_prompt=system_prompt,
                )
            except Exception:
                pass  # Trajectory capture is best-effort; never crash the run

            return result.stdout.strip(), task_input, run_log, island_topology, image, trajectory
        finally:
            shutil.rmtree(state_root_tmp, ignore_errors=True)

    @staticmethod
    def _write_cage_opencode_json(
        workspace_host_path: Path,
        opencode_provider: str,
        opencode_model_id: str,
    ) -> Path:
        """Write cage-run opencode.json in workspace root."""
        prompt = load_prompt_text(
            Path(__file__).resolve().parent / "prompts" / "executor.yaml",
            "cage_opencode_prompt",
            default="",
        )
        config = {
            "$schema": "https://opencode.ai/config.schema.json",
            "model": f"{opencode_provider}/{opencode_model_id}",
            "mode": {
                "build": {
                    "prompt": prompt,
                }
            },
            "provider": {
                opencode_provider: {
                    "models": {
                        opencode_model_id: {},
                    }
                }
            },
        }
        out_path = workspace_host_path / "opencode.json"
        out_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return out_path

    @staticmethod
    def _load_review_nudge() -> str:
        """Load review nudge text from executor.yaml."""
        return (
            load_prompt_text(
                Path(__file__).resolve().parent / "prompts" / "executor.yaml",
                "cage_opencode_review_nudge",
                default="",
            )
            or ""
        )

    @staticmethod
    def _compute_opencode_model_binding(
        mut_provider: str,
        mut_model: str,
        runtime_config: Dict[str, Any],
        cli_model_override: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Compute OpenCode model binding from MUT provider/model.

        Args:
            mut_provider: MUT provider (e.g., "groq", "openai")
            mut_model: MUT model name (e.g., "gemma2-9b-it", "openai/gpt-oss-120b")
            runtime_config: Runtime configuration dict
            cli_model_override: If set, the raw --model CLI arg was provided
                explicitly and takes precedence over ``runtime_opencode_model``
                in the config.

        Returns:
            Tuple of (provider, model_id) for OpenCode binding
        """
        # Check for explicit override — skipped when the CLI --model flag was
        # provided, because user intent (CLI) must win over static config.
        if cli_model_override is None:
            override_model = runtime_config.get("runtime_opencode_model")
            if override_model:
                if "/" in override_model:
                    provider, model_id = override_model.split("/", 1)
                    return provider.strip(), model_id.strip()
                else:
                    # Override without provider defaults to MUT provider
                    return mut_provider, override_model.strip()

        provider = mut_provider
        model_id = mut_model

        return provider, model_id

    @staticmethod
    def _get_provider_api_keys(provider: str) -> Dict[str, str]:
        """Get provider API keys from host environment.

        Args:
            provider: Provider name (e.g., "groq", "openai", "anthropic")

        Returns:
            Dict of env var name -> value for API keys that exist in host env
        """
        provider_env_map = {
            "groq": "GROQ_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "xai": "XAI_API_KEY",
        }

        api_keys = {}
        env_var = provider_env_map.get(provider.lower())
        if env_var:
            env_value = os.environ.get(env_var)
            if env_value:
                api_keys[env_var] = env_value

        return api_keys

    @staticmethod
    def _read_workspace_system_prompt(workspace_path: Path) -> Optional[str]:
        """Extract ``mode.build.prompt`` from workspace ``opencode.json``."""
        opencode_json_path = workspace_path / "opencode.json"
        if not opencode_json_path.exists():
            return None

        try:
            config = json.loads(opencode_json_path.read_text(encoding="utf-8"))
            if isinstance(config, dict):
                mode = config.get("mode", {})
                if isinstance(mode, dict):
                    build = mode.get("build", {})
                    if isinstance(build, dict):
                        return build.get("prompt")
        except Exception:
            pass

        return None

    @staticmethod
    def _opencode_sessions_dir(xdg_data_home: Optional[Path] = None) -> Optional[Path]:
        """Find OpenCode sessions directory.

        Args:
            xdg_data_home: Optional XDG_DATA_HOME path for run-scoped storage

        Returns:
            Path to sessions directory, or None if not found
        """
        if xdg_data_home is not None:
            # Use run-scoped XDG_DATA_HOME first (Fix 1)
            opencode_base = xdg_data_home / "opencode" / "storage"
            message_dir = opencode_base / "message"
            if message_dir.exists():
                return message_dir
            session_dir = opencode_base / "session"
            if session_dir.exists():
                return session_dir
            return None

        # Fall back to global ~/.local/share path
        try:
            base = Path.home() / ".local" / "share" / "opencode" / "storage"
            message_dir = base / "message"
            if message_dir.exists():
                return message_dir
            session_dir = base / "session"
            if session_dir.exists():
                return session_dir
        except Exception:
            pass
        return None

    @staticmethod
    def _snapshot_session_ids(sessions_dir: Optional[Path]) -> set[str]:
        """Get set of session IDs from sessions directory.

        Args:
            sessions_dir: Path to sessions directory

        Returns:
            Set of session IDs (directory names)
        """
        if not sessions_dir or not sessions_dir.exists():
            return set()
        try:
            return {d.name for d in sessions_dir.iterdir() if d.is_dir()}
        except Exception:
            return set()

    @staticmethod
    def _pick_newest_session(sessions_dir: Path, session_ids: set[str]) -> Optional[Path]:
        """Pick newest session by directory modification time from given set.

        Args:
            sessions_dir: Path to sessions directory
            session_ids: Set of session IDs to consider

        Returns:
            Newest session Path, or None if not found
        """
        if not sessions_dir or not sessions_dir.exists() or not session_ids:
            return None
        try:
            sessions = [(d, d.stat().st_mtime) for d in sessions_dir.iterdir() if d.is_dir() and d.name in session_ids]
            if not sessions:
                return None
            newest = sorted(sessions, key=lambda x: x[1], reverse=True)[0][0]
            return newest
        except Exception:
            return None

    @staticmethod
    def _pick_session_by_mtime(sessions_dir: Path, window_start: datetime, window_end: datetime) -> Optional[Path]:
        """Pick session modified within time window.

        Args:
            sessions_dir: Path to sessions directory
            window_start: Start of time window
            window_end: End of time window

        Returns:
            Session Path if modified within window, or None if not found
        """
        if not sessions_dir or not sessions_dir.exists():
            return None
        try:
            for d in sessions_dir.iterdir():
                if d.is_dir():
                    mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
                    if window_start <= mtime <= window_end:
                        return d
            return None
        except Exception:
            return None

    @staticmethod
    def _normalise_message(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a message from OpenCode storage.

        Args:
            raw: Raw message dict from storage

        Returns:
            Normalized message dict
        """
        msg: Dict[str, Any] = {
            "role": str(raw.get("role", "unknown")),
            "content": "",
        }

        # Handle content as string or list
        content = raw.get("content", "")
        if isinstance(content, list):
            # Join text fields from list format
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    text_parts.append(str(item.get("text", "")))
                else:
                    text_parts.append(str(item))
            msg["content"] = "".join(text_parts)
        else:
            msg["content"] = str(content)

        if "tool_calls" in raw:
            tool_calls = raw["tool_calls"]
            if tool_calls:
                try:
                    msg["tool_calls"] = tool_calls if isinstance(tool_calls, list) else [tool_calls]
                except Exception:
                    pass
        if "tool_call_id" in raw:
            msg["tool_call_id"] = str(raw["tool_call_id"])
        return msg

    def _build_trajectory(
        self,
        session_dir: Path,
        test_case_id: str,
        model_str: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]:
        """Build trajectory from OpenCode session directory.

        Args:
            session_dir: Path to session directory
            test_case_id: Test case ID
            model_str: Model string
            start_time: Run start time
            end_time: Run end time

        Returns:
            Trajectory dict with messages and stats
        """
        messages = []
        input_tokens = 0
        output_tokens = 0

        if session_dir and session_dir.exists():
            # Read all JSON files in session directory
            try:
                for msg_file in sorted(session_dir.glob("*.json")):
                    try:
                        raw = json.loads(msg_file.read_text(encoding="utf-8"))
                        msg = self._normalise_message(raw)
                        messages.append(msg)

                        # Extract token counts from usage field (Fix 3)
                        usage = raw.get("usage", {})
                        if isinstance(usage, dict):
                            try:
                                input_tokens += int(usage.get("input_tokens", 0))
                                output_tokens += int(usage.get("output_tokens", 0))
                            except (ValueError, TypeError):
                                # Non-numeric token values are silently ignored (Fix 3)
                                pass
                    except (json.JSONDecodeError, Exception):
                        # Skip malformed JSON files
                        pass
            except Exception:
                pass

        total_turns = len(messages)

        trajectory = {
            "instance_id": test_case_id,
            "model": model_str,
            "created_at": start_time.isoformat(),
            "ended_at": end_time.isoformat(),
            "messages": messages,
            "stats": {
                "total_turns": total_turns,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_seconds": (end_time - start_time).total_seconds(),
            },
        }

        return trajectory

    def _save_runtime_artifacts(self, result: TestResult) -> None:
        """Save runtime artifacts to results directory.

        Args:
            result: TestResult with runtime_artifacts dict
        """
        if not hasattr(self, "results_outdir") or not self.results_outdir:
            return

        artifacts = result.runtime_artifacts
        if not artifacts:
            return

        retention = str(self.evaluation_config.get("runtime_artifact_retention", "standard")).lower()

        # Determine output path
        test_id = result.test_case.id
        if result.trials_total > 1:
            # Multi-trial: use trial subdirectory
            outdir = self.results_outdir / "runtime" / test_id / f"trial_{result.trial}"
        else:
            outdir = self.results_outdir / "runtime" / test_id

        outdir.mkdir(parents=True, exist_ok=True)

        # Save trajectory if present and retention allows
        if "trajectory.json" in artifacts and retention in ("standard", "full"):
            trajectory = artifacts["trajectory.json"]
            if trajectory:
                trajectory_path = outdir / "trajectory.json"
                trajectory_path.write_text(json.dumps(trajectory, indent=2), encoding="utf-8")

        # Save metadata.json with trial fields if present
        if "metadata.json" in artifacts:
            metadata = artifacts["metadata.json"]
            if metadata:
                # Add trial fields if multi-trial
                if result.trials_total > 1:
                    metadata = dict(metadata)  # Make a copy to avoid mutating input
                    metadata["trial"] = result.trial
                    metadata["trials_total"] = result.trials_total
                metadata_path = outdir / "metadata.json"
                metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        # In minimal mode, persist metadata only.
        if retention == "minimal":
            return

        # Save run log if present and retention allows
        if "run.log" in artifacts and retention in ("standard", "full"):
            run_log = artifacts["run.log"]
            if run_log:
                run_log_path = outdir / "run.log"
                run_log_path.write_text(str(run_log), encoding="utf-8")

        # Save checks if present and retention allows
        if "checks.json" in artifacts and retention in ("standard", "full"):
            checks = artifacts["checks.json"]
            if checks:
                checks_path = outdir / "checks.json"
                checks_path.write_text(json.dumps(checks, indent=2), encoding="utf-8")

        # Save final diff if present and retention allows
        if "final.diff" in artifacts and retention in ("standard", "full"):
            final_diff = artifacts["final.diff"]
            if final_diff:
                final_diff_path = outdir / "final.diff"
                final_diff_path.write_text(str(final_diff), encoding="utf-8")

    @staticmethod
    def _extract_trajectory_tool_names(trajectory: Dict[str, Any]) -> set[str]:
        """Extract normalized tool names used in trajectory messages."""
        used_tools: set[str] = set()
        messages = trajectory.get("messages")
        if not isinstance(messages, list):
            return used_tools

        for message in messages:
            if not isinstance(message, dict):
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue

            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                tool_name: Optional[str] = None
                if isinstance(function, dict):
                    name = function.get("name")
                    if name:
                        tool_name = str(name)
                elif isinstance(function, str):
                    tool_name = function
                elif tool_call.get("name"):
                    tool_name = str(tool_call.get("name"))

                if tool_name:
                    used_tools.add(tool_name.strip().lower())

        return used_tools

    @staticmethod
    def _parse_rejected_tool_attempts(run_log: str) -> List[Dict[str, str]]:
        """Parse rejected tool attempts from run.log output.

        Handles two error patterns:
          1. "attempted to call tool 'X' which was not in request.tools"
             when a tool is called that was not included in the request's tools list.
          2. "parameters for tool X did not match schema: missing properties: 'Y'"
             when a tool is called with invalid/missing parameters.

        Args:
            run_log: The run.log string (stdout + stderr combined)

        Returns:
            List of dicts with keys: tool_name (str), error_message (str)
        """
        rejected: List[Dict[str, str]] = []
        if not run_log:
            return rejected

        import re

        # Pattern 1: "attempted to call tool 'TOOL_NAME' which was not in request.tools"
        # Also handles variants like: "attempted to call tool 'TOOL_NAME' ..."
        pattern_rejected = re.compile(
            r"attempted to call tool ['\"]([^'\"]+)['\"](.*?)(?:\n|$)",
            re.IGNORECASE,
        )
        for match in pattern_rejected.finditer(run_log):
            tool_name = match.group(1).strip().lower()
            error_extra = match.group(2).strip()
            rejected.append(
                {
                    "tool_name": tool_name,
                    "error_message": (
                        f"attempted to call tool '{tool_name}' which was not in request.tools {error_extra}"
                    ).strip(),
                }
            )

        # Pattern 2: "parameters for tool TOOL_NAME did not match schema: ..."
        # Example: "parameters for tool read did not match schema: missing properties: 'filePath'"
        pattern_schema = re.compile(
            r"parameters for tool ['\"]?([^'\"\n]+?)['\"]? did not match schema[:\s]+([^\n]*)",
            re.IGNORECASE,
        )
        for match in pattern_schema.finditer(run_log):
            tool_name = match.group(1).strip().lower()
            error_detail = match.group(2).strip()
            rejected.append(
                {
                    "tool_name": tool_name,
                    "error_message": f"parameters for tool '{tool_name}' did not match schema: {error_detail}".strip(),
                }
            )

        return rejected

    @classmethod
    def _build_tool_allowlist_check(
        cls,
        trajectory: Optional[Dict[str, Any]],
        rejected_tool_attempts: Optional[List[Dict[str, str]]] = None,
        enforce: bool = False,
    ) -> Optional[CheckResult]:
        """Build deterministic tool allowlist check from trajectory and rejected attempts.

        Args:
            trajectory: Trajectory dict from OpenCode session
            rejected_tool_attempts: Optional list of rejected attempt dicts with 'tool_name' keys
            enforce: If True, check fails hard on disallowed tools. If False (default),
                     check passes but records disallowed/rejected attempts in details.

        Returns:
            CheckResult if trajectory is present, None otherwise
        """
        if not trajectory and not rejected_tool_attempts:
            return None

        used_tools = cls._extract_trajectory_tool_names(trajectory) if trajectory else set()

        # Include rejected tool attempts in the union of used/disallowed tools
        if rejected_tool_attempts:
            rejected_tool_names = {
                attempt["tool_name"].strip().lower() for attempt in rejected_tool_attempts if attempt.get("tool_name")
            }
            used_tools = used_tools | rejected_tool_names

        allowed_tools = {"bash", "read", "write", "edit"}
        disallowed_tools = sorted(tool for tool in used_tools if tool not in allowed_tools)

        passed = not disallowed_tools if enforce else True

        return CheckResult(
            name="tool_allowlist_guard",
            type="tool_allowlist",
            passed=passed,
            message=(
                "Only allowlisted tools were used"
                if not disallowed_tools
                else f"Disallowed tools detected: {', '.join(disallowed_tools)}"
            ),
            is_critical=enforce,
            details={
                "allowed_tools": sorted(allowed_tools),
                "used_tools": sorted(used_tools),
                "disallowed_tools": disallowed_tools,
                "rejected_tool_attempts": rejected_tool_attempts or [],
                "enforce_mode": enforce,
            },
        )

    def execute_runtime_test(self, test_case: TestCaseSpec, trial: int = 0) -> TestResult:
        """Execute a runtime testcase and capture runtime artifacts."""
        result = TestResult(self.framework, self.category, test_case, self.mut_model_str, self.judge_model_str)
        runtime_config = self.evaluation_config
        runtime_mode = str(runtime_config.get("runtime_mode", "cage"))
        effective_runtime_mode = "cage" if runtime_mode in ("cage", "container") else runtime_mode
        runtime_timeout_seconds = int(runtime_config.get("runtime_timeout_seconds", 1800))
        keep_workspace = bool(runtime_config.get("runtime_keep_workspaces", False))

        workspace_dir: Optional[Path] = None
        workspace: Any
        source = test_case.raw.get("source") if test_case.raw else None
        environment = test_case.raw.get("environment") if test_case.raw else None
        use_runtime_workspace = isinstance(source, dict) and isinstance(environment, dict)

        class _RuntimeWorkspace:
            def __init__(self, path: Path):
                self.path = path
                self.ddev_project_name = ""

        if use_runtime_workspace:
            assert isinstance(source, dict)
            assert isinstance(environment, dict)
            validate_runtime_testcase(test_case)
            file_path = Path(test_case.file_path) if test_case.file_path else Path.cwd()
            repo_root = find_git_root(file_path)
            branch_name = source.get("task_branch") or source.get("base_branch")
            if branch_name:
                test_case.resolved_sha = resolve_branch_to_sha(branch_name, repo_root)
            workspace = Workspace(base_path=Path("workspaces"), task_id=test_case.id)
            workspace.create(source_path=repo_root, sha=test_case.resolved_sha)
            setup_mode = str(environment.get("setup_mode", "config_import"))
            post_setup_commands = environment.get("post_setup_commands")
            workspace.ddev_start(
                setup_mode=setup_mode,
                timeout=runtime_timeout_seconds,
                post_setup_commands=post_setup_commands if isinstance(post_setup_commands, list) else None,
            )
            # Enforce 1:1 developer command contract before MUT starts.
            workspace._run_logged_command(["ddev", "status"], timeout=runtime_timeout_seconds)
            workspace._run_logged_command(
                ["ddev", "drush", "status", "--fields=bootstrap,drupal-version"],
                timeout=runtime_timeout_seconds,
            )
            workspace_dir = workspace.path
        else:
            workspace_dir = Path(tempfile.mkdtemp(prefix=f"nichebench-runtime-{test_case.id}-"))
            workspace = _RuntimeWorkspace(workspace_dir)

        profile = resolve_profile("offline_cli")
        try:
            self._run_runtime_preflight_host(runtime_config, runtime_mode)
            self._run_runtime_preflight_workspace(workspace_dir, effective_runtime_mode)
            self._inject_task_markdown(workspace_dir, test_case)
            checks_config = self._load_runtime_checks(test_case)

            if effective_runtime_mode == "cage":
                (
                    mut_output,
                    user_input,
                    run_log,
                    island_topology,
                    effective_image,
                    trajectory,
                    retry_info,
                ) = self._run_container_runtime_task_with_retry(
                    test_case=test_case,
                    workspace=workspace,
                    agent_manifest=test_case.raw.get("agent", {}),
                    runtime_config=runtime_config,
                    profile=profile,
                    timeout_seconds=runtime_timeout_seconds,
                )
            else:
                raise ValidationError(f"Unsupported runtime mode: {effective_runtime_mode}")

            # Two-pass review nudge: run a second MUT pass with review nudge as fresh user message
            enable_review_nudge = bool(runtime_config.get("runtime_enable_review_nudge", True))
            review_pass_info: Optional[Dict[str, Any]] = None
            first_pass_mut_output = mut_output
            first_pass_run_log = run_log

            if enable_review_nudge and effective_runtime_mode == "cage":
                review_nudge = self._load_review_nudge()
                if review_nudge:
                    # Run second pass with review nudge as task_input_override
                    # (NOT appended to TASK.md - the nudge is delivered as a fresh user message)
                    (
                        mut_output,
                        user_input,
                        run_log,
                        island_topology,
                        effective_image,
                        trajectory,
                        _,
                    ) = self._run_container_runtime_task_with_retry(
                        test_case=test_case,
                        workspace=workspace,
                        agent_manifest=test_case.raw.get("agent", {}),
                        runtime_config=runtime_config,
                        profile=profile,
                        timeout_seconds=runtime_timeout_seconds,
                        task_input_override=review_nudge,
                    )
                    review_pass_info = {
                        "first_pass_output": first_pass_mut_output,
                        "first_pass_run_log": first_pass_run_log,
                        "attempted": True,
                    }

            result.user_input = user_input
            result.mut_output = mut_output

            # Parse rejected tool attempts from run.log
            rejected_tool_attempts = self._parse_rejected_tool_attempts(run_log)

            result.runtime_artifacts = {
                "run.log": run_log,
                "metadata.json": self._build_runtime_metadata(
                    test_case=test_case,
                    profile=profile,
                    runtime_mode=runtime_mode,
                    runtime_config=runtime_config,
                    workspace=workspace,
                    island_topology=island_topology,
                    effective_image=effective_image,
                    retry_info=retry_info,
                    review_pass_info=review_pass_info,
                ),
            }
            # Store first pass output in artifacts when review nudge was used
            if review_pass_info:
                result.runtime_artifacts["review_pass_output"] = {
                    "first_pass_output": review_pass_info.get("first_pass_output", ""),
                    "first_pass_run_log": review_pass_info.get("first_pass_run_log", ""),
                }
            if (workspace_dir / ".git").exists():
                try:
                    if hasattr(workspace, "capture_final_diff"):
                        result.runtime_artifacts["final.diff"] = workspace.capture_final_diff(test_case.resolved_sha)
                except Exception:
                    pass
            if trajectory is not None:
                result.runtime_artifacts["trajectory.json"] = trajectory

            scorer = RuntimeScorer(
                workspace_path=workspace_dir,
                command_timeout_seconds=runtime_timeout_seconds,
            )
            check_results = scorer.run_deterministic_checks(checks_config)
            tool_allowlist_enforce = bool(runtime_config.get("runtime_tool_allowlist_enforce", False))
            tool_allowlist_check = self._build_tool_allowlist_check(
                trajectory, rejected_tool_attempts, enforce=tool_allowlist_enforce
            )
            if tool_allowlist_check is not None:
                check_results.append(tool_allowlist_check)
            checks_payload = [
                {
                    "name": c.name,
                    "type": c.type,
                    "passed": c.passed,
                    "message": c.message,
                    "critical": c.is_critical,
                    "details": c.details,
                }
                for c in check_results
            ]
            result.runtime_artifacts["checks.json"] = {"deterministic": checks_payload}

            judge_score: Optional[float] = None
            runtime_judge_output: Dict[str, Any] = {}
            llm_judge_config = test_case.raw.get("llm_judge", {})
            if llm_judge_config.get("checklist"):
                judge_samples = int(runtime_config.get("runtime_judge_samples", 1))
                judge_samples = max(1, judge_samples)

                sample_scores: List[float] = []
                last_judge_output: Dict[str, Any] = {}
                for _ in range(judge_samples):
                    last_judge_output, _ = self.judge_runner.evaluate_test(
                        test_case,
                        "runtime",
                        user_input,
                        mut_output,
                        self.judge_system_prompt,
                        runtime_artifacts=result.runtime_artifacts,
                    )
                    sample_scores.append(float(last_judge_output.get("overall_score", 0.0)))

                judge_score = statistics.median(sample_scores)
                runtime_judge_output = last_judge_output
                if judge_samples > 1:
                    runtime_judge_output["judge_sample_scores"] = sample_scores
                    runtime_judge_output["judge_sample_median"] = judge_score

            hybrid_score = scorer.compute_hybrid_score(
                check_results=check_results,
                judge_score=judge_score,
                scoring_config=test_case.raw.get("scoring", {}),
            )

            result.judge_output = {
                "deterministic_score": hybrid_score.deterministic_score,
                "judge_score": hybrid_score.judge_score,
                "hybrid_score": hybrid_score.final_score,
                "final_score": hybrid_score.final_score,
                "deterministic_gate_passed": not any(c.is_critical and not c.passed for c in check_results),
                "checks": checks_payload,
                "runtime_judge": runtime_judge_output,
            }
            result.passed = hybrid_score.passed
        except Exception as exc:
            result.error = str(exc)
            result.passed = False
            result.mut_output = f"[Error: {exc}]"
            result.judge_output = {"error": str(exc)}
        finally:
            if use_runtime_workspace and hasattr(workspace, "cleanup"):
                # Always release DDEV project resources (containers/networks/volumes)
                # so repeated runs do not leak Docker networks.
                workspace.cleanup(
                    timeout=runtime_timeout_seconds,
                    remove_workspace=not keep_workspace,
                )
            else:
                shutil.rmtree(workspace_dir, ignore_errors=True)

        return result

    @staticmethod
    def _build_trajectory_from_sqlite(
        db_path: Path,
        test_case_id: str,
        model_str: str,
        start_time: datetime,
        end_time: datetime,
        system_prompt: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build trajectory payload from OpenCode SQLite database.

        Args:
            db_path: Path to opencode.db file
            test_case_id: Test case ID for metadata
            model_str: Model string used for the run
            start_time: Run start time
            end_time: Run end time
            system_prompt: System prompt used (optional)

        Returns:
            Trajectory dict with messages and stats, or None if DB not found/invalid
        """
        if not db_path.exists():
            return None

        try:
            import sqlite3

            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Check if sessions table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='session'")
            if not cursor.fetchone():
                # Primary schema not found — attempt legacy schema fallback (sessions, plural)
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
                if not cursor.fetchone():
                    conn.close()
                    return None

                # Legacy schema: sessions/messages tables with plain columns
                cursor.execute("SELECT id FROM sessions ORDER BY created_at DESC")
                legacy_sessions = cursor.fetchall()
                if not legacy_sessions:
                    conn.close()
                    return None
                legacy_session_id = legacy_sessions[0][0]
                cursor.execute(
                    "SELECT role, content, tool_calls, created_at FROM messages "
                    "WHERE session_id = ? ORDER BY created_at ASC",
                    (legacy_session_id,),
                )
                legacy_rows = cursor.fetchall()
                conn.close()

                legacy_messages: List[Dict[str, Any]] = []
                for legacy_row in legacy_rows:
                    legacy_role, legacy_content, legacy_tool_calls_json, legacy_created_at = legacy_row
                    legacy_msg: Dict[str, Any] = {
                        "role": legacy_role,
                        "content": legacy_content,
                        "created_at": legacy_created_at,
                    }
                    if legacy_tool_calls_json:
                        try:
                            legacy_msg["tool_calls"] = json.loads(legacy_tool_calls_json)
                        except Exception:
                            pass
                    legacy_messages.append(legacy_msg)

                legacy_trajectory: Dict[str, Any] = {
                    "instance_id": test_case_id,
                    "model": model_str,
                    "created_at": start_time.isoformat(),
                    "ended_at": end_time.isoformat(),
                    "messages": legacy_messages,
                    "stats": {
                        "total_turns": len([m for m in legacy_messages if m["role"] == "assistant"]),
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "duration_seconds": (end_time - start_time).total_seconds(),
                    },
                }
                if system_prompt:
                    legacy_trajectory["system_prompt"] = system_prompt
                return legacy_trajectory

            # Get all sessions
            cursor.execute("SELECT id FROM session ORDER BY time_created DESC")
            sessions = cursor.fetchall()

            if not sessions:
                return None

            # Get the newest session
            session_id = sessions[0][0]

            # Get messages for this session
            cursor.execute(
                "SELECT id, session_id, data, time_created FROM message "
                "WHERE session_id = ? ORDER BY time_created ASC",
                (session_id,),
            )
            message_rows = cursor.fetchall()

            # Get parts for messages (support both real and legacy schemas)
            cursor.execute("PRAGMA table_info(part)")
            part_columns = {row[1] for row in cursor.fetchall()}
            has_legacy_type_column = "type" in part_columns
            if has_legacy_type_column:
                cursor.execute(
                    "SELECT id, message_id, type, data, time_created FROM part "
                    "WHERE message_id IN (SELECT id FROM message WHERE session_id = ?) "
                    "ORDER BY time_created ASC",
                    (session_id,),
                )
            else:
                cursor.execute(
                    "SELECT id, message_id, data, time_created FROM part "
                    "WHERE message_id IN (SELECT id FROM message WHERE session_id = ?) "
                    "ORDER BY time_created ASC",
                    (session_id,),
                )
            part_rows = cursor.fetchall()

            conn.close()

            # Build messages map
            messages_map: Dict[str, Dict[str, Any]] = {}
            trajectory_input_tokens = 0
            trajectory_output_tokens = 0
            for msg_id, msg_session_id, msg_data, msg_time in message_rows:
                try:
                    msg_json = json.loads(msg_data)
                    usage = msg_json.get("usage", {})
                    if isinstance(usage, dict):
                        input_tokens = usage.get("input_tokens", 0)
                        output_tokens = usage.get("output_tokens", 0)
                        if isinstance(input_tokens, (int, float)):
                            trajectory_input_tokens += int(input_tokens)
                        if isinstance(output_tokens, (int, float)):
                            trajectory_output_tokens += int(output_tokens)

                    tokens = msg_json.get("tokens", {})
                    if isinstance(tokens, dict):
                        input_tokens = tokens.get("input", 0)
                        output_tokens = tokens.get("output", 0)
                        if isinstance(input_tokens, (int, float)):
                            trajectory_input_tokens += int(input_tokens)
                        if isinstance(output_tokens, (int, float)):
                            trajectory_output_tokens += int(output_tokens)

                    # Check if message has content field
                    has_content = "content" in msg_json and msg_json["content"]
                    messages_map[msg_id] = {
                        "role": msg_json.get("role", "unknown"),
                        "content": msg_json.get("content", ""),
                        "created_at": msg_time,
                        "tool_calls": msg_json.get("tool_calls"),
                        "tool_call_id": msg_json.get("tool_call_id"),
                        "_has_content": has_content,  # Track if original had content
                        "_text_parts": [],  # Collect text parts for rebuilding
                        "_thinking_parts": [],  # Collect thinking parts
                        "_all_parts": [],  # Collect all parts
                    }
                except Exception:
                    pass

            # Merge parts into messages (thinking/reasoning/text)
            for part_row in part_rows:
                if has_legacy_type_column:
                    part_id, msg_id, legacy_part_type, part_data, part_time = part_row
                else:
                    part_id, msg_id, part_data, part_time = part_row
                    legacy_part_type = ""
                if msg_id in messages_map:
                    try:
                        part_json = json.loads(part_data)
                        if not isinstance(part_json, dict):
                            part_json = {"text": str(part_json)}
                        part_type = str(legacy_part_type or part_json.get("type", ""))
                        if part_type and "type" not in part_json:
                            part_json["type"] = part_type
                        # Store all parts
                        messages_map[msg_id]["_all_parts"].append(part_json)

                        if part_type in ("thinking", "reasoning"):
                            # Collect thinking parts
                            thinking_text = part_json.get("text") or part_json.get("data", "")
                            if thinking_text:
                                messages_map[msg_id]["_thinking_parts"].append({"text": thinking_text})
                        elif part_type == "text":
                            # Collect text parts for rebuilding if original had no content
                            if not messages_map[msg_id].get("_has_content", False):
                                messages_map[msg_id]["_text_parts"].append(part_json.get("text", ""))
                    except Exception:
                        pass

            # Rebuild content from text parts if needed and finalize messages
            for msg_id in messages_map:
                # Rebuild content from text parts if original had no content
                if not messages_map[msg_id].get("_has_content", False) and messages_map[msg_id].get("_text_parts"):
                    messages_map[msg_id]["content"] = "\n".join(messages_map[msg_id]["_text_parts"])

                # Add thinking parts if any
                if messages_map[msg_id].get("_thinking_parts"):
                    messages_map[msg_id]["thinking"] = messages_map[msg_id]["_thinking_parts"]

                # Add all parts if any
                if messages_map[msg_id].get("_all_parts"):
                    messages_map[msg_id]["parts"] = messages_map[msg_id]["_all_parts"]

                # Remove internal tracking fields
                messages_map[msg_id].pop("_has_content", None)
                messages_map[msg_id].pop("_text_parts", None)
                messages_map[msg_id].pop("_thinking_parts", None)
                messages_map[msg_id].pop("_all_parts", None)

            # Build messages list in order
            messages = []
            for msg_id, _, _, _ in message_rows:
                if msg_id in messages_map:
                    msg = messages_map[msg_id]
                    # Clean up tool_calls if None
                    if msg.get("tool_calls") is None:
                        msg.pop("tool_calls", None)
                    if msg.get("tool_call_id") is None:
                        msg.pop("tool_call_id", None)
                    messages.append(msg)

            trajectory = {
                "instance_id": test_case_id,
                "model": model_str,
                "created_at": start_time.isoformat(),
                "ended_at": end_time.isoformat(),
                "messages": messages,
                "stats": {
                    "total_turns": len(messages),
                    "input_tokens": trajectory_input_tokens,
                    "output_tokens": trajectory_output_tokens,
                    "duration_seconds": (end_time - start_time).total_seconds(),
                },
            }

            if system_prompt:
                trajectory["system_prompt"] = system_prompt

            return trajectory

        except Exception:
            return None
