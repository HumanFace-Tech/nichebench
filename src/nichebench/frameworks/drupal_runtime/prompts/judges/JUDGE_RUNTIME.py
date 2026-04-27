from pathlib import Path

from nichebench.core.prompt_loader import load_prompt_text

# System prompt for the LLM judge on drupal_runtime tasks.
# This is the static fallback. Task manifests may override this via llm_judge.model_role.
JUDGE_RUNTIME_SYSTEM_PROMPT = load_prompt_text(
    Path(__file__).with_name("judge_runtime.yaml"),
    "system_prompt",
    default="",
)
