from pathlib import Path

from nichebench.core.prompt_loader import load_prompt_text

JUDGE_CODE_GENERATION_SYSTEM_PROMPT = load_prompt_text(
    Path(__file__).with_name("judge_code_generation.yaml"),
    "system_prompt",
    default="",
)
