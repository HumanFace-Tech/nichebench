from pathlib import Path

from nichebench.core.prompt_loader import load_prompt_text

QUIZ_SYSTEM_PROMPT = load_prompt_text(Path(__file__).with_name("quiz.yaml"), "system_prompt", default="")
