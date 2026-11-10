"""The removed ``code_agent`` category is rejected, never executed.

Regression coverage for the ``code_agent`` / LangGraph removal:

* ``MUTRunner.run_test`` raises for ``code_agent`` before any model call.
* ``dispatch.execute_test`` raises before any runner is invoked — a stub
  runner proves nothing is executed as another type.
* ``nichebench run <framework> code_agent`` fails fast with a clear message.
* The LangGraph provider package and the ``CODE_AGENT`` prompt module are
  gone, and no ``langchain``/``langgraph`` dependency remains declared.
* The active paths (``quiz``, ``code_generation``, ``bug_fixing``,
  ``runtime``) still route exactly as before.

No network access and no model endpoint is required.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from typer.testing import CliRunner

from nichebench.cli.app import app
from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.dispatch import execute_test
from nichebench.execution.runners.mut import MUTRunner

REPO_ROOT = Path(__file__).resolve().parents[3]

cli_runner = CliRunner()


class _RecordingClient:
    """Stand-in for :class:`LiteLLMClient` that records every call."""

    def __init__(self, output: str = "model output") -> None:
        self.output = output
        self.calls: List[Tuple[str, Any]] = []

    def generate(self, prompt: str, model: str, model_params: Any = None) -> Dict[str, Any]:
        self.calls.append(("generate", prompt))
        return {"model": model, "output": self.output}

    def generate_with_messages(self, messages: Any, model: str, model_params: Any = None) -> Dict[str, Any]:
        self.calls.append(("messages", messages))
        return {"model": model, "output": self.output}


class _ForbiddenClient:
    """Client that fails the test if any model call is attempted."""

    def generate(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise AssertionError("the MUT must not be called for a removed category")

    def generate_with_messages(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise AssertionError("the MUT must not be called for a removed category")


class _ForbiddenRunner:
    """Runner double that fails the test if anything invokes it."""

    def __init__(self, name: str = "runner") -> None:
        self.name = name

    def __getattr__(self, attribute: str) -> Any:
        raise AssertionError(f"the {self.name} must not run this task")


def _mut_runner(client: Any) -> MUTRunner:
    runner = MUTRunner(
        model_str="openai/gpt-4o",
        model_config={"parameters": {}},
        timeout=5,
        retry_attempts=1,
        retry_delay=0,
    )
    runner.client = client
    return runner


def _quiz_case() -> TestCaseSpec:
    return TestCaseSpec(
        id="quiz_001",
        type="quiz",
        raw={"question": "Which letter?", "choices": ["a", "b"]},
        choices=["a", "b"],
        correct_choice="B",
    )


def _code_case() -> TestCaseSpec:
    return TestCaseSpec(id="code_001", type="code", raw={"prompt": "Write a function."}, prompt="Write a function.")


def _bug_case() -> TestCaseSpec:
    return TestCaseSpec(id="bug_001", type="bug", raw={"prompt": "Fix the crash."}, prompt="Fix the crash.")


# ─── explicit rejection ───────────────────────────────────────────────────────


def test_mut_runner_rejects_code_agent_before_any_model_call() -> None:
    mut = _mut_runner(_ForbiddenClient())

    with pytest.raises(ValueError, match="code_agent"):
        mut.run_test(_code_case(), None, "code_agent")


def test_dispatch_rejects_code_agent_before_invoking_any_runner() -> None:
    """A legacy task must never be executed as another category."""
    with pytest.raises(ValueError, match="code_agent"):
        execute_test(
            test_case=_code_case(),
            category="code_agent",
            framework="drupal",
            mut_model_str="openai/gpt-4o",
            judge_model_str="openai/gpt-4o",
            mut_runner=_ForbiddenRunner("MUT"),
            judge_runner=_ForbiddenRunner("judge"),
            system_prompt=None,
            judge_system_prompt=None,
        )


def test_cli_run_rejects_code_agent_with_a_clear_message() -> None:
    result = cli_runner.invoke(app, ["run", "drupal", "code_agent"])

    assert result.exit_code != 0
    assert "code_agent" in result.output
    assert "removed" in result.output
    # The message points at the replacement category.
    assert "runtime" in result.output


# ─── the implementation is really gone ───────────────────────────────────────


def test_langgraph_provider_package_is_gone() -> None:
    assert importlib.util.find_spec("nichebench.providers.langgraph_code_agent") is None


def test_code_agent_prompt_module_is_gone() -> None:
    prompts_dir = REPO_ROOT / "src" / "nichebench" / "frameworks" / "drupal" / "prompts"
    assert importlib.util.find_spec("nichebench.frameworks.drupal.prompts.CODE_AGENT") is None
    assert not (prompts_dir / "code_agent.yaml").exists()


def test_pyproject_declares_no_langchain_dependency() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        poetry = tomllib.load(handle)["tool"]["poetry"]

    declared = set(poetry["dependencies"]) | set(poetry["group"]["dev"]["dependencies"])
    offenders = sorted(name for name in declared if name.startswith(("langchain", "langgraph")))
    assert offenders == []


def test_no_langchain_or_langgraph_reference_in_source() -> None:
    hits: List[str] = []
    src_root = REPO_ROOT / "src"
    for path in src_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if "langchain" in text or "langgraph" in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


# ─── unaffected active paths ──────────────────────────────────────────────────


def test_quiz_still_runs_single_turn() -> None:
    client = _RecordingClient(output="B")
    output, user_input = _mut_runner(client).run_test(_quiz_case(), None, "quiz")

    assert output == "B"
    assert "Question: Which letter?" in user_input
    assert [call[0] for call in client.calls] == ["generate"]


def test_code_generation_still_runs_single_turn() -> None:
    client = _RecordingClient(output="```python\nprint('ok')\n```")
    output, user_input = _mut_runner(client).run_test(_code_case(), None, "code_generation")

    assert output.startswith("```python")
    assert "Write a function." in user_input
    assert [call[0] for call in client.calls] == ["generate"]


def test_bug_fixing_still_runs_multi_turn() -> None:
    class _TwoTurnClient:
        """Answers once, then signals completion with ``FINAL``."""

        def __init__(self) -> None:
            self.calls: List[Tuple[str, Any]] = []

        def generate_with_messages(self, messages: Any, model: str, model_params: Any = None) -> Dict[str, Any]:
            self.calls.append(("messages", messages))
            output = "Fixed the crash." if len(self.calls) == 1 else "FINAL"
            return {"model": model, "output": output}

        def generate(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
            raise AssertionError("bug_fixing must not use the single-turn path")

    client = _TwoTurnClient()
    output, user_input = _mut_runner(client).run_test(_bug_case(), None, "bug_fixing")

    assert output == "Fixed the crash."
    assert "Fix the crash." in user_input
    assert len(client.calls) == 2


def test_dispatch_still_routes_runtime_to_the_runtime_executor() -> None:
    sentinel = object()

    def _runtime_executor(test_case: TestCaseSpec, trial: int = 0) -> Any:
        return sentinel

    result = execute_test(
        test_case=_code_case(),
        category="runtime",
        framework="drupal_runtime",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_ForbiddenRunner("MUT"),
        judge_runner=_ForbiddenRunner("judge"),
        system_prompt=None,
        judge_system_prompt=None,
        execute_runtime_test_fn=_runtime_executor,
    )

    assert result is sentinel


def test_dispatch_still_executes_code_generation_end_to_end() -> None:
    """quiz/code_generation/bug_fixing keep the MUT → judge pipeline."""

    class _StubJudge:
        def evaluate_test(
            self,
            test_case: TestCaseSpec,
            category: str,
            user_input: str,
            mut_output: str,
            judge_system_prompt: Optional[str],
            runtime_artifacts: Optional[Dict[str, Any]] = None,
        ) -> Tuple[Dict[str, Any], bool]:
            assert category == "code_generation"
            return {"overall_score": 0.9}, True

    result = execute_test(
        test_case=_code_case(),
        category="code_generation",
        framework="drupal",
        mut_model_str="openai/gpt-4o",
        judge_model_str="openai/gpt-4o",
        mut_runner=_mut_runner(_RecordingClient(output="code")),
        judge_runner=_StubJudge(),
        system_prompt=None,
        judge_system_prompt=None,
    )

    assert result.passed is True
    assert result.mut_output == "code"
    assert result.judge_output == {"overall_score": 0.9}
