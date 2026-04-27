from pathlib import Path

from nichebench.core.prompt_loader import load_prompt_text

JUDGE_BUG_FIXING_SYSTEM_PROMPT = load_prompt_text(
    Path(__file__).with_name("judge_bug_fixing.yaml"),
    "system_prompt",
    default="",
)
