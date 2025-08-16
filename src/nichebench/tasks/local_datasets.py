"""Custom dataset integration for NicheBench YAML data."""

from pathlib import Path
from typing import Any, Dict

from .data_loader import load_yaml_samples

# datasets package types are dynamic; use a generic Any for Dataset typing
Dataset = Any


def create_local_dataset(category: str, task_name: str) -> Dict[str, Any]:
    """Create a HuggingFace Dataset from local YAML files."""
    data_dir = Path(__file__).parent / "drupal" / "data"

    # Collect all samples
    samples = []
    for doc, raw_data in load_yaml_samples(data_dir, category, task_name):
        # Convert to dataset format
        sample = {
            "doc_id": raw_data.get("id", ""),
            "context": raw_data.get("context", ""),
            "reference": raw_data.get("reference", ""),
        }

        if category == "quiz":
            sample.update(
                {
                    "question": raw_data.get("question", ""),
                    "choices": raw_data.get("choices", []),
                    "correct_choice": raw_data.get("correct_choice", "A"),
                    "gold_index": 0,  # Will be computed from correct_choice
                }
            )

            # Compute gold_index from correct_choice
            choices = raw_data.get("choices", [])
            correct_choice = raw_data.get("correct_choice", "A").strip().upper()
            if choices:
                # Extract choice letters from choices
                choice_letters = []
                for choice in choices:
                    if choice.strip().startswith(("A)", "B)", "C)", "D)", "E)")):
                        choice_letters.append(choice.strip()[0])
                    else:
                        choice_letters.append(chr(ord("A") + len(choice_letters)))

                if correct_choice in choice_letters:
                    sample["gold_index"] = choice_letters.index(correct_choice)
        else:
            sample.update(
                {
                    "prompt": raw_data.get("prompt", ""),
                    "judge_checklist": raw_data.get("judge_checklist", []),
                }
            )

        samples.append(sample)

    # Create dataset
    if not samples:
        samples = [{"doc_id": "dummy", "context": "", "reference": ""}]

    dataset = Dataset.from_list(samples)
    return {"test": dataset}


# Register datasets when imported
def register_local_datasets() -> None:
    """Register local datasets with LightEval's dataset cache."""
    try:
        import lighteval

        # Create a global registry if it doesn't exist
        if not hasattr(lighteval, "_LOCAL_DATASET_CACHE"):
            lighteval._LOCAL_DATASET_CACHE = {}

        # Register our datasets
        datasets_to_create = [
            ("quiz", "nichebench_drupal_quiz"),
            ("code_generation", "nichebench_drupal_code_generation"),
            ("bug_fixing", "nichebench_drupal_bug_fixing"),
        ]

        for category, task_name in datasets_to_create:
            cache_key = f"local/{task_name}"
            if cache_key not in lighteval._LOCAL_DATASET_CACHE:
                lighteval._LOCAL_DATASET_CACHE[cache_key] = create_local_dataset(
                    category, task_name
                )

    except ImportError:
        pass  # Skip if datasets not available


# Auto-register when module loads
register_local_datasets()
