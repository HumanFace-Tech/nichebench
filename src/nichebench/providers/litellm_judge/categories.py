"""Category-specific prompt builders for the judge LLM.

Each ``score_*`` method in ``LiteLLMJudge`` follows a documented *prompt
contract* stored in
``nichebench/providers/prompts/litellm_judge.yaml``. This module contains
the pure functions that compose the prompt strings for each category.

Ownership
=========
This module is owned by the ``litellm_judge`` package. The functions here
are called exclusively by ``judge.py``. They have no side effects and no
external dependencies beyond ``_PROMPTS`` (loaded once at package import).

Public API
==========
These functions are not exported from the package ``__init__.py`` — they
are internal to the judge pipeline. External callers should use
``LiteLLMJudge.score_*`` methods.

Prompt contract summary
========================
Every judge prompt follows the same structure:

1. Optional ``system_prompt`` overlay (from framework pack).
2. Default system role (from YAML).
3. Task-specific context (question, code, bug description, etc.).
4. Checklist criteria (for code_gen, bug_fixing, runtime).
5. JSON contract instruction (from YAML).

The judge **must** return a valid JSON object conforming to the contract.
"""

from pathlib import Path
from typing import Any, Optional

from nichebench.core.prompt_loader import load_prompt_mapping

_PROMPTS = load_prompt_mapping(Path(__file__).resolve().parent.parent / "prompts" / "litellm_judge.yaml")


# ---------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------


def build_quiz_prompt(
    *,
    question: str,
    choices: list[str],
    gold: str,
    candidate: str,
    system_prompt: Optional[str] = None,
    judge_notes: Optional[str] = None,
) -> str:
    """Build the judge prompt for a quiz item.

    Args:
        question: the question text.
        choices: list of answer option strings.
        gold: the correct answer letter.
        candidate: the model's answer letter.
        system_prompt: optional system-level overlay.
        judge_notes: optional additional context.

    Returns:
        The full prompt string to send to the judge LLM.
    """
    choices_text = "\n".join([f"{chr(65+i)}. {c}" for i, c in enumerate(choices)]) if choices else ""

    prompt_parts = []
    if system_prompt:
        prompt_parts.append(system_prompt.strip())
    prompt_parts.append(str(_PROMPTS.get("quiz_default_role", "You are an evaluation judge.")))
    prompt_parts.append(f"Question: {question}")
    if choices_text:
        prompt_parts.append(f"Choices:\n{choices_text}")
    prompt_parts.append(f"Gold (correct answer): {gold}")
    prompt_parts.append(f"Model answer: {candidate}")

    if judge_notes:
        prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
        prompt_parts.append(str(_PROMPTS.get("quiz_notes_hint", "")))

    prompt_parts.append(str(_PROMPTS.get("quiz_json_contract", "")))

    return "\n\n".join(prompt_parts)


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def build_code_generation_prompt(
    *,
    prompt: str,
    generated_code: str,
    checklist: list[str],
    system_prompt: Optional[str] = None,
    judge_notes: Optional[str] = None,
) -> str:
    """Build the judge prompt for a code generation task.

    Args:
        prompt: the original user task prompt.
        generated_code: the model's generated code.
        checklist: list of evaluation criteria strings.
        system_prompt: optional system-level overlay.
        judge_notes: optional additional context.

    Returns:
        The full prompt string to send to the judge LLM.
    """
    checklist_text = "\n".join([f"- {item}" for item in checklist]) if checklist else "No specific criteria provided"

    prompt_parts = []
    if system_prompt:
        prompt_parts.append(system_prompt.strip())
    else:
        prompt_parts.append(str(_PROMPTS.get("code_default_role", "You are an expert code reviewer.")))

    prompt_parts.append(str(_PROMPTS.get("code_eval_intro", "Evaluate the following code implementation:")))
    prompt_parts.append(f"Task/Prompt: {prompt}")
    prompt_parts.append(f"Generated Code:\n{generated_code}")
    prompt_parts.append(f"Checklist Criteria:\n{checklist_text}")

    if judge_notes:
        prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
        prompt_parts.append(str(_PROMPTS.get("code_notes_hint", "")))

    prompt_parts.append(str(_PROMPTS.get("code_json_contract", "")))

    return "\n\n".join(prompt_parts)


# ---------------------------------------------------------------------------
# Bug fixing
# ---------------------------------------------------------------------------


def build_bug_fixing_prompt(
    *,
    bug_description: str,
    proposed_fix: str,
    checklist: list[str],
    system_prompt: Optional[str] = None,
    judge_notes: Optional[str] = None,
) -> str:
    """Build the judge prompt for a bug fixing task.

    Args:
        bug_description: description of the bug being fixed.
        proposed_fix: the model's proposed fix.
        checklist: list of evaluation criteria strings.
        system_prompt: optional system-level overlay.
        judge_notes: optional additional context.

    Returns:
        The full prompt string to send to the judge LLM.
    """
    checklist_text = "\n".join([f"- {item}" for item in checklist]) if checklist else "No specific criteria provided"

    prompt_parts = []
    if system_prompt:
        prompt_parts.append(system_prompt.strip())
    else:
        prompt_parts.append(
            str(_PROMPTS.get("bug_default_role", "You are an expert code reviewer evaluating bug fixes."))
        )

    prompt_parts.append(str(_PROMPTS.get("bug_eval_intro", "Evaluate the following bug fix:")))
    prompt_parts.append(f"Bug Description: {bug_description}")
    prompt_parts.append(f"Proposed Fix:\n{proposed_fix}")
    prompt_parts.append(f"Checklist Criteria:\n{checklist_text}")

    if judge_notes:
        prompt_parts.append(f"Additional Context for Evaluation:\n{judge_notes.strip()}")
        prompt_parts.append(str(_PROMPTS.get("bug_notes_hint", "")))

    prompt_parts.append(str(_PROMPTS.get("bug_json_contract", "")))

    return "\n\n".join(prompt_parts)


# ---------------------------------------------------------------------------
# Runtime (drupal_runtime tasks)
# ---------------------------------------------------------------------------


def build_runtime_prompt(
    *,
    task_description: str,
    artifact_summary: str,
    checklist_items: list[dict[str, Any]],
    system_prompt: Optional[str] = None,
) -> str:
    """Build the judge prompt for a runtime (agentic) task.

    Args:
        task_description: human-readable task overview.
        artifact_summary: concatenated diff / log / checks text.
        checklist_items: manifest checklist dicts with ``id``, ``question``,
            ``weight``, ``guidance``, and ``bonus`` keys.
        system_prompt: optional system-level overlay.

    Returns:
        The full prompt string to send to the judge LLM.
    """
    if not checklist_items:
        raise ValueError("checklist_items must not be empty for runtime scoring")

    # Build the checklist block for the prompt
    checklist_parts: list[str] = []
    for item in checklist_items:
        item_id = str(item.get("id", "unknown"))
        question = str(item.get("question", "")).strip()
        weight = float(item.get("weight", 1.0))
        guidance = str(item.get("guidance", "")).strip()
        bonus = bool(item.get("bonus", False))

        entry = f"criterion_id: {item_id}\nQuestion: {question}\nWeight: {weight}"
        if bonus:
            entry += "\n(BONUS — do not penalise if absent)"
        if guidance:
            entry += f"\nGuidance: {guidance}"
        checklist_parts.append(entry)

    checklist_text = "\n\n---\n\n".join(checklist_parts)

    prompt_parts: list[str] = []
    if system_prompt:
        prompt_parts.append(system_prompt.strip())
    else:
        prompt_parts.append(
            str(
                _PROMPTS.get(
                    "runtime_default_role",
                    "You are an expert evaluating an AI agent's Drupal engineering work.",
                )
            )
        )

    prompt_parts.append("## Task Description\n\n" + task_description.strip())
    prompt_parts.append("## Agent Artifacts\n\n" + artifact_summary)
    prompt_parts.append(str(_PROMPTS.get("runtime_checklist_intro", "")) + "\n\n" + checklist_text)
    prompt_parts.append(str(_PROMPTS.get("runtime_json_contract", "")))

    return "\n\n".join(prompt_parts)
