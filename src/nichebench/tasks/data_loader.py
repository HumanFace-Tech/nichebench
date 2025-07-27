"""Data loading utilities for NicheBench hardcoded samples."""

from typing import Any, Dict, Iterator, List

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
