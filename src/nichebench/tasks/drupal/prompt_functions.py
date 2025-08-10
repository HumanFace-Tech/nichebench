"""Prompt functions for Drupal-specific tasks in NicheBench."""

import re
from pathlib import Path
from typing import Any, Dict, Optional

from lighteval.tasks.requests import Doc


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
    """
    Create a prompt for Drupal quiz questions.

    Args:
        line: Data line containing quiz information
        task_name: Name of the task

    Returns:
        Doc: Formatted document for evaluation
    """
    # Load system prompt
    system_prompt = load_system_prompt("quiz.txt")

    # Format the question with choices
    question_text = line.get("question") or line.get("prompt") or ""
    query = f"{system_prompt}\n\nQuestion: {question_text}\n\n"

    # Add choices if present
    if "choices" in line:
        for i, choice in enumerate(line["choices"]):
            # Ensure choices are labeled (A), (B), etc., if not already
            label = chr(ord("A") + i)
            rendered = choice
            if not re.match(r"^[A-Z]\)", str(choice).strip()):
                rendered = f"{label}) {choice}"
            query += f"{rendered}\n"

    query += "\nAnswer:"

    # Create Doc object
    if "choices" in line:
        # Multiple choice format
        doc = Doc(
            task_name=task_name,
            query=query,
            choices=line["choices"],
            gold_index=line.get("gold_index", 0),
            specific={
                "id": line.get("id", ""),
                "context": line.get("context", ""),
                "reference": line.get("reference", ""),
            },
        )
    else:
        # Open-ended format
        doc = Doc(
            task_name=task_name,
            query=query,
            gold_index=0,
            specific={
                "id": line.get("id", ""),
                "context": line.get("context", ""),
                "reference": line.get("reference", ""),
            },
        )
        doc._golds = [line.get("reference", "")]

    return doc


def drupal_code_generation_prompt(
    line: Dict[str, Any], task_name: Optional[str] = None
) -> Doc:
    """
    Create a prompt for Drupal code generation tasks.

    Args:
        line: Data line containing code generation task
        task_name: Name of the task

    Returns:
        Doc: Formatted document for evaluation
    """
    # Load system prompt
    system_prompt = load_system_prompt("code_generation.txt")

    # Format the prompt
    query = (
        f"{system_prompt}\n\nTask: {line['prompt']}\n\n"
        "Provide your complete code solution:"
    )

    # Create Doc object
    doc = Doc(
        task_name=task_name,
        query=query,
        gold_index=0,
        specific={
            "id": line.get("id", ""),
            "context": line.get("context", ""),
            "judge_checklist": line.get("judge_checklist", []),
        },
    )
    doc._golds = [""]  # No reference solution for AI judge evaluation

    return doc


def drupal_bug_fixing_prompt(
    line: Dict[str, Any], task_name: Optional[str] = None
) -> Doc:
    """
    Create a prompt for Drupal bug fixing tasks.

    Args:
        line: Data line containing bug fixing task
        task_name: Name of the task

    Returns:
        Doc: Formatted document for evaluation
    """
    # Load system prompt
    system_prompt = load_system_prompt("bug_fixing.txt")

    # Format the prompt
    query = (
        f"{system_prompt}\n\nProblem: {line['prompt']}\n\n"
        "Provide the corrected code:"
    )

    # Create Doc object
    doc = Doc(
        task_name=task_name,
        query=query,
        gold_index=0,
        specific={
            "id": line.get("id", ""),
            "context": line.get("context", ""),
            "judge_checklist": line.get("judge_checklist", []),
        },
    )
    doc._golds = [""]  # No reference solution for AI judge evaluation

    return doc
