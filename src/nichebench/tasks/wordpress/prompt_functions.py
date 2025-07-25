"""
Prompt generation functions for WordPress tasks.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from lighteval.tasks.lighteval_task import Doc


def load_system_prompt(prompt_file: str) -> str:
    """Load system prompt from file."""
    current_dir = Path(__file__).parent
    prompt_path = current_dir / "system_prompts" / prompt_file
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def wordpress_plugin_quiz_prompt(
    line: Dict[str, Any], task_name: Optional[str] = None
) -> Doc:
    """Prompt function for WordPress plugin quiz questions."""
    system_prompt = load_system_prompt("plugin_quiz.txt")
    context = line.get("context", "")
    question = line.get("prompt", "")

    if context:
        full_prompt = (
            f"{system_prompt}\n\nContext: {context}\n\nQuestion: {question}\n\n"
            "Please provide a comprehensive answer that demonstrates your "
            "understanding of WordPress best practices."
        )
    else:
        full_prompt = (
            f"{system_prompt}\n\nQuestion: {question}\n\n"
            "Please provide a comprehensive answer that demonstrates your "
            "understanding of WordPress best practices."
        )

    return Doc(
        task_name=task_name,
        query=full_prompt,
        choices=[""],  # For generative tasks
        gold_index=0,
        instruction="",
    )


def wordpress_plugin_generation_prompt(
    line: Dict[str, Any], task_name: Optional[str] = None
) -> Doc:
    """Prompt function for WordPress plugin generation tasks."""
    system_prompt = load_system_prompt("plugin_generation.txt")
    requirements = line.get("prompt", "")
    context = line.get("context", "")

    if context:
        full_prompt = (
            f"{system_prompt}\n\n"
            "Generate a WordPress plugin that meets the following requirements:\n\n"
            f"Requirements: {requirements}\n\n"
            f"Additional Context: {context}\n\n"
            "Include the main plugin file and any necessary additional files."
        )
    else:
        full_prompt = (
            f"{system_prompt}\n\n"
            "Generate a WordPress plugin that meets the following requirements:\n\n"
            f"Requirements: {requirements}\n\n"
            "Include the main plugin file and any necessary additional files."
        )

    return Doc(
        task_name=task_name,
        query=full_prompt,
        choices=[""],  # For generative tasks
        gold_index=0,
        instruction="",
    )
