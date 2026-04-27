from pathlib import Path

from nichebench.core.prompt_loader import load_prompt_text

JUDGE_QUIZ_SYSTEM_PROMPT = load_prompt_text(
    Path(__file__).with_name("judge_quiz.yaml"),
    "system_prompt",
    default="",
)
