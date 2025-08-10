"""Data loading utilities for NicheBench samples (YAML-backed or hardcoded)."""

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml  # type: ignore
from lighteval.tasks.requests import Doc


def create_doc_from_sample(sample_data: Dict[str, Any], task_name: str) -> Doc:
    """
    Create a LightEval Doc object from our hardcoded sample data.

    Args:
        sample_data: Dictionary containing sample data
        task_name: Name of the task

    Returns:
        Doc: LightEval Doc object
    """
    # Handle quiz samples (multiple choice)
    if "choices" in sample_data:
        doc = Doc(
            task_name=task_name,
            query=sample_data["prompt"],
            choices=sample_data["choices"],
            gold_index=sample_data["gold_index"],
            specific={
                "id": sample_data["id"],
                "context": sample_data["context"],
                "checklist": sample_data["checklist"],
                "reference": sample_data["reference"],
            },
        )
    else:
        # Handle code generation and bug fixing samples (generative)
        doc = Doc(
            task_name=task_name,
            query=sample_data["prompt"],
            gold_index=0,  # Single reference answer
            specific={
                "id": sample_data["id"],
                "context": sample_data["context"],
                "checklist": sample_data["checklist"],
                "reference": sample_data["reference"],
            },
        )
        # Set the gold reference manually
        doc._golds = [sample_data["reference"]]

    return doc


def load_hardcoded_samples(
    sample_data: Dict[str, List[Dict]], task_type: str, task_name: str
) -> Iterator[Doc]:
    """
    Load hardcoded samples as LightEval Doc objects.

    Args:
        sample_data: Dictionary containing all sample data
        task_type: Type of task (e.g., "quiz", "code_generation", "bug_fixing")
        task_name: Name of the task for LightEval

    Yields:
        Doc: LightEval Doc objects
    """
    if task_type not in sample_data:
        return

    for sample in sample_data[task_type]:
        yield create_doc_from_sample(sample, task_name)


def get_sample_count(sample_data: Dict[str, List[Dict]], task_type: str) -> int:
    """Get the number of samples for a given task type."""
    return len(sample_data.get(task_type, []))


# --- YAML-based loader (Drupal) -------------------------------------------------


def _iter_yaml_files(root: Path) -> Iterator[Path]:
    for p in sorted(root.glob("*.yaml")):
        if p.is_file():
            yield p


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}
        return data


def load_yaml_samples(
    base_dir: Path, category: str, task_name: str
) -> Iterator[Tuple[Doc, Dict[str, Any]]]:
    """
    Load YAML samples from base_dir/<category>/*.yaml.

    Returns pairs of (Doc for test AI, raw dict) so callers can also build a
    judge prompt using the raw data.
    """
    category_dir = base_dir / category
    if not category_dir.exists():
        return

    for file in _iter_yaml_files(category_dir):
        data = _load_yaml(file)
        if not data:
            continue

        # Normalize fields for Doc creation
        doc_specific = {
            "id": data.get("id", file.stem),
            "context": data.get("context", ""),
            # For quiz, no checklist; for others, optional checklist is OK
            "checklist": [] if category == "quiz" else data.get("checklist", []),
            "reference": data.get("reference", data.get("solution_includes", "")),
        }

        if category == "quiz":
            query = data.get("question") or data.get("prompt") or ""
            choices = data.get("choices", [])
            gold_index = 0
            # If correct_choice is given as letter, map to index for convenience
            correct_choice = (data.get("correct_choice") or "").strip().upper()
            if correct_choice and choices:
                labels = [c.split(")", 1)[0].strip().upper() for c in choices]
                if correct_choice in labels:
                    gold_index = labels.index(correct_choice)

            doc = Doc(
                task_name=task_name,
                query=query,
                choices=choices,
                gold_index=gold_index,
                specific=doc_specific,
            )
            yield doc, data

        else:
            # code_generation or bug_fixing are generative
            query = data.get("prompt") or data.get("summary") or ""
            doc = Doc(
                task_name=task_name,
                query=query,
                gold_index=0,
                specific=doc_specific,
            )
            # Gold can be a textual reference or a list of inclusion hints
            reference = data.get("reference") or data.get("solution_includes") or ""
            doc._golds = [reference if isinstance(reference, str) else str(reference)]
            yield doc, data


def build_test_prompt_from_yaml(category: str, data: Dict[str, Any]) -> str:
    """Build the test-AI prompt body from YAML sample fields."""
    parts: List[str] = []
    ctx = data.get("context")
    if ctx:
        parts.append(f"Context:\n{ctx}")
    if category == "quiz":
        parts.append(f"Question: {data.get('question') or data.get('prompt') or ''}")
        choices = data.get("choices", [])
        if choices:
            parts.append("Choices:")
            parts.extend(choices)
    else:
        parts.append(f"Task: {data.get('prompt') or data.get('summary') or ''}")
    return "\n\n".join(parts).strip()


def build_judge_prompt_from_yaml(
    category: str, data: Dict[str, Any], model_answer: str
) -> str:
    """Build the judge-AI prompt body, including the expected answer signal."""
    parts: List[str] = []
    parts.append("You are a strict evaluator. Reply with only 'YES' or 'NO'.")
    ctx = data.get("context")
    if ctx:
        parts.append(f"Context:\n{ctx}")
    if category == "quiz":
        parts.append(f"Question: {data.get('question') or data.get('prompt') or ''}")
        parts.append("Choices:")
        for c in data.get("choices", []):
            parts.append(c)
        parts.append(f"Model answer: {model_answer}")
        parts.append(f"Correct choice: {data.get('correct_choice', '')}")
        parts.append(
            "Decision: Did the model pick the correct choice? Reply 'YES' or 'NO'."
        )
    else:
        parts.append(f"Task: {data.get('prompt') or data.get('summary') or ''}")
        inc = data.get("solution_includes", [])
        if inc:
            parts.append("Required elements:")
            for item in inc if isinstance(inc, list) else [inc]:
                parts.append(f"- {item}")
        parts.append(
            "Assess whether the model's patch satisfies the requirements "
            "and checklist. Reply 'YES' or 'NO'."
        )
    return "\n\n".join(parts).strip()
