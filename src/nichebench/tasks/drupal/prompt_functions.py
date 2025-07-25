"""
Prompt functions for Drupal tasks following LightEval conventions.
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


def drupal_quiz_prompt(line: Dict[str, Any], task_name: Optional[str] = None) -> Doc:
    """Prompt function for Drupal quiz questions with multiple choice answers."""
    system_prompt = load_system_prompt("quiz.txt")

    # Extract data from the dataset line
    context = line.get("context", "")
    summary = line.get("summary", "")
    question = line.get("question", "")
    choices = line.get("choices", "")

    # Build the full prompt
    prompt_parts = [system_prompt]

    if context:
        prompt_parts.append(f"Context: {context}")

    if summary:
        prompt_parts.append(f"Summary: {summary}")

    prompt_parts.append(f"Question: {question}")

    if choices:
        prompt_parts.append(f"Choices:\n{choices}")

    prompt_parts.append("Answer: ")

    full_prompt = "\n\n".join(prompt_parts)

    return Doc(
        task_name=task_name,
        query=full_prompt,
        choices=[""],  # For generative tasks
        gold_index=0,
        instruction="",
    )


def drupal_code_generation_prompt(
    line: Dict[str, Any], task_name: Optional[str] = None
) -> Doc:
    """Prompt function for Drupal code generation tasks."""
    system_prompt = load_system_prompt("code_generation.txt")

    # Extract data from the dataset line
    context = line.get("context", "")
    summary = line.get("summary", "")
    task_description = line.get("task", line.get("question", ""))

    # Build the full prompt
    prompt_parts = [system_prompt]

    if context:
        prompt_parts.append(f"Context: {context}")

    if summary:
        prompt_parts.append(f"Summary: {summary}")

    prompt_parts.append(f"Task: {task_description}")
    prompt_parts.append("Please implement the complete solution:")

    full_prompt = "\n\n".join(prompt_parts)

    return Doc(
        task_name=task_name,
        query=full_prompt,
        choices=[""],  # For generative tasks
        gold_index=0,
        instruction="",
    )


def drupal_bug_fixing_prompt(
    line: Dict[str, Any], task_name: Optional[str] = None
) -> Doc:
    """Prompt function for Drupal bug fixing tasks."""
    system_prompt = load_system_prompt("bug_fixing.txt")

    # Extract data from the dataset line
    context = line.get("context", "")
    summary = line.get("summary", "")
    task_description = line.get("task", line.get("question", ""))

    # Build the full prompt
    prompt_parts = [system_prompt]

    if context:
        prompt_parts.append(f"Context: {context}")

    if summary:
        prompt_parts.append(f"Summary: {summary}")

    prompt_parts.append(f"Problem: {task_description}")
    prompt_parts.append("Please provide the complete fix:")

    full_prompt = "\n\n".join(prompt_parts)

    return Doc(
        task_name=task_name,
        query=full_prompt,
        choices=[""],  # For generative tasks
        gold_index=0,
        instruction="",
    )
