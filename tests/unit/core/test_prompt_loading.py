from pathlib import Path

from nichebench.core.prompt_loader import load_prompt_text
from nichebench.execution.orchestrator import TestExecutor
from nichebench.execution.runtime.opencode_config import PROMPTS_PATH
from nichebench.frameworks.drupal.prompts.BUG_FIXING import BUG_FIXING_SYSTEM_PROMPT
from nichebench.frameworks.drupal.prompts.CODE_AGENT import (
    CODE_AGENT_PLANNER_REQUEST_TEMPLATE,
    CODE_AGENT_SOLVER_REQUEST_TEMPLATE,
)
from nichebench.frameworks.drupal.prompts.CODE_GENERATION import (
    CODE_GENERATION_SYSTEM_PROMPT,
)
from nichebench.frameworks.drupal.prompts.judges.JUDGE_CODE_GENERATION import (
    JUDGE_CODE_GENERATION_SYSTEM_PROMPT,
)
from nichebench.frameworks.drupal.prompts.QUIZ import QUIZ_SYSTEM_PROMPT
from nichebench.frameworks.drupal_runtime.prompts.judges.JUDGE_RUNTIME import (
    JUDGE_RUNTIME_SYSTEM_PROMPT,
)


def test_framework_prompt_constants_loaded_from_yaml() -> None:
    assert QUIZ_SYSTEM_PROMPT.startswith("You are a senior Drupal developer")
    assert BUG_FIXING_SYSTEM_PROMPT.startswith("You are a senior Drupal 11 developer")
    assert CODE_GENERATION_SYSTEM_PROMPT.startswith("You are a Drupal developer implementing a feature")
    assert JUDGE_CODE_GENERATION_SYSTEM_PROMPT.startswith("You are an expert Drupal code reviewer")
    assert JUDGE_RUNTIME_SYSTEM_PROMPT.startswith("You are a senior Drupal 11 engineer")


def test_code_agent_request_templates_keep_placeholders() -> None:
    assert "{original_task}" in CODE_AGENT_PLANNER_REQUEST_TEMPLATE
    assert "{context_block}" in CODE_AGENT_PLANNER_REQUEST_TEMPLATE
    assert "{step_number}" in CODE_AGENT_SOLVER_REQUEST_TEMPLATE
    assert "{current_step}" in CODE_AGENT_SOLVER_REQUEST_TEMPLATE


def test_write_cage_opencode_json_uses_yaml_prompt(tmp_path: Path) -> None:
    opencode_json = TestExecutor._write_cage_opencode_json(
        workspace_host_path=tmp_path,
        opencode_provider="groq",
        opencode_model_id="gemma2-9b-it",
    )
    expected = load_prompt_text(
        PROMPTS_PATH,
        "cage_opencode_prompt",
        default="",
    )
    saved = TestExecutor._read_workspace_system_prompt(tmp_path)

    assert opencode_json.exists()
    assert saved == expected
