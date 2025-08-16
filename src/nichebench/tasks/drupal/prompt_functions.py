"""Prompt functions for Drupal-specific tasks in NicheBench."""

import re
from pathlib import Path
from typing import Any, Dict, Optional

from lighteval.tasks.requests import Doc, RequestType


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

    # Add choices to the query if present
    if "choices" in line:
        for i, choice in enumerate(line["choices"]):
            # Ensure choices are labeled (A), (B), etc., if not already
            label = chr(ord("A") + i)
            rendered = choice
            if not re.match(r"^[A-Z]\)", str(choice).strip()):
                rendered = f"{label}) {choice}"
            query += f"{rendered}\n"

    query += "\nAnswer with just the letter (A, B, C, D, or E):"

    # Create Doc object for generative approach
    # For generative tasks, we need at least one choice.
    # The choice won't be used for loglikelihood scoring.
    doc = Doc(
        task_name=task_name,
        query=query,
        choices=[""],  # Single empty choice for generative mode
        gold_index=0,
        specific={
            "id": line.get("id", ""),
            "context": line.get("context", ""),
            "reference": line.get("reference", ""),
            "choices": line.get("choices", []),  # Store actual choices here for metric
            "correct_choice": line.get("correct_choice", "A"),  # Store correct answer
        },
    )

    return doc


def drupal_code_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Wrapper for drupal_code_generation_prompt for backward compatibility."""
    return drupal_code_generation_prompt(line, task_name)


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

    # Debug: print what we're actually getting
    print("[DEBUG] Code gen prompt received keys: {}".format(list(line.keys())))

    # Handle different data formats
    prompt_text = line.get(
        "prompt", line.get("question", line.get("text", "No prompt found"))
    )

    # Format the prompt
    query = (
        f"{system_prompt}\n\nTask: {prompt_text}\n\n"
        "Provide your complete code solution:"
    )

    # Create Doc object
    doc = Doc(
        task_name=task_name,
        query=query,
        choices=[""],  # Required empty choices for generative tasks
        gold_index=0,
        specific={
            "id": line.get("id", ""),
            "context": line.get("context", ""),
            "judge_checklist": line.get("judge_checklist", []),
        },
    )

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

    # Debug: print what we're actually getting
    print(f"[DEBUG] Bug fixing prompt received keys: {line.keys()}")
    print(f"[DEBUG] Bug fixing prompt data: {line}")

    # Handle different data formats - try to extract prompt from various possible fields
    prompt_text = ""
    if "prompt" in line:
        prompt_text = line["prompt"]
    elif "question" in line:
        prompt_text = line["question"]
    elif "text" in line:
        prompt_text = line["text"]
    else:
        # Fallback - show all available keys
        print(f"[ERROR] No 'prompt' field found. Available keys: {list(line.keys())}")
        prompt_text = "No prompt found"

    # Format the prompt
    query = (
        f"{system_prompt}\n\nProblem: {prompt_text}\n\n" "Provide the corrected code:"
    )

    # Create Doc object
    doc = Doc(
        task_name=task_name,
        query=query,
        choices=[""],  # Required empty choices for generative tasks
        gold_index=0,
        specific={
            "id": line.get("id", ""),
            "context": line.get("context", ""),
            "judge_checklist": line.get("judge_checklist", []),
        },
    )

    return doc
