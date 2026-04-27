from pathlib import Path

from nichebench.core.prompt_loader import load_prompt_text

BUG_FIXING_SYSTEM_PROMPT = load_prompt_text(
    Path(__file__).with_name("bug_fixing.yaml"),
    "system_prompt",
    default="",
)
