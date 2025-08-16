"""Drupal-specific evaluation tasks for NicheBench."""

from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from lighteval.metrics.metrics import MetricCategory, Metrics, MetricUseCase
from lighteval.metrics.utils.metric_utils import SampleLevelMetric
from lighteval.tasks.lighteval_task import LightevalTaskConfig
from lighteval.tasks.requests import Doc

from ...metrics.checklist import checklist_accuracy_fn


def quiz_accuracy_fn(
    predictions: List[str], formatted_doc: Doc, **kwargs: Any
) -> float:
    """Strict boolean quiz accuracy: 1.0 if choice letter matches gold, else 0.0."""
    if not predictions:
        return 0.0
    pred_raw = (predictions[0] or "").strip().upper()
    # Extract a single letter A-E from the prediction
    import re

    m = re.search(r"[A-E]", pred_raw)
    pred_letter = m.group(0) if m else ""

    # Get correct answer from specific dict
    correct_choice = formatted_doc.specific.get("correct_choice", "A")

    return 1.0 if pred_letter == correct_choice else 0.0


def code_quality_fn(predictions: List[str], formatted_doc: Doc, **kwargs: Any) -> float:
    """Evaluate code generation output via dataset-provided checklist (0..1)."""
    if not predictions:
        return 0.0
    # checklist_accuracy_fn expects (predictions, formatted_doc)
    return checklist_accuracy_fn(predictions, formatted_doc)


def bug_fixing_fn(predictions: List[str], formatted_doc: Doc, **kwargs: Any) -> float:
    """Evaluate bug-fix output via dataset-provided checklist (0..1)."""
    if not predictions:
        return 0.0
    return checklist_accuracy_fn(predictions, formatted_doc)


# Define metrics for Drupal tasks
# Quiz metric using generative approach for API compatibility
drupal_quiz_metric = SampleLevelMetric(
    metric_name="drupal_quiz_accuracy",
    sample_level_fn=quiz_accuracy_fn,
    category=MetricCategory.GENERATIVE,  # Changed from MULTICHOICE to GENERATIVE
    use_case=MetricUseCase.ACCURACY,
    corpus_level_fn=np.mean,
    higher_is_better=True,
)

drupal_code_metric = SampleLevelMetric(
    metric_name="drupal_code_quality",
    sample_level_fn=code_quality_fn,
    category=MetricCategory.GENERATIVE,
    use_case=MetricUseCase.CODE,
    corpus_level_fn=np.mean,
    higher_is_better=True,
)

drupal_bug_fixing_metric = SampleLevelMetric(
    metric_name="drupal_bug_fixing",
    sample_level_fn=bug_fixing_fn,
    category=MetricCategory.GENERATIVE,
    use_case=MetricUseCase.CODE,
    corpus_level_fn=np.mean,
    higher_is_better=True,
)


# Prompt function wrappers for LightEval compatibility
def drupal_quiz_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for quiz data - works with HuggingFace dataset."""
    from .prompt_functions import drupal_quiz_prompt

    return drupal_quiz_prompt(line, task_name)


def drupal_code_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for code generation data - works with HuggingFace dataset."""
    from .prompt_functions import drupal_code_generation_prompt

    return drupal_code_generation_prompt(line, task_name)


def drupal_bug_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for bug fixing data - works with HuggingFace dataset."""
    from .prompt_functions import drupal_bug_fixing_prompt

    return drupal_bug_fixing_prompt(line, task_name)


# Task configurations using local HuggingFace datasets
drupal_quiz_task = LightevalTaskConfig(
    name="nichebench_drupal_quiz",
    prompt_function=drupal_quiz_hardcoded_prompt,
    suite=["community"],
    hf_repo="datasets/drupal_quiz",  # Use parquet loader
    hf_subset="default",
    hf_avail_splits=["train"],
    evaluation_splits=["train"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[Metrics.exact_match],
    generation_size=5,
    stop_sequence=["\n"],
    trust_dataset=False,
)

drupal_code_task = LightevalTaskConfig(
    name="nichebench_drupal_code_generation",
    prompt_function=drupal_code_hardcoded_prompt,
    suite=["community"],
    hf_repo="datasets/drupal_code_generation",  # Use parquet loader
    hf_subset="default",
    hf_avail_splits=["train"],
    evaluation_splits=["train"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[drupal_code_metric],
    generation_size=512,
    stop_sequence=["```", "\n\n"],
    trust_dataset=False,
)

drupal_bug_task = LightevalTaskConfig(
    name="nichebench_drupal_bug_fixing",
    prompt_function=drupal_bug_hardcoded_prompt,
    suite=["community"],
    hf_repo="datasets/drupal_bug_fixing",  # Use parquet loader
    hf_subset="default",
    hf_avail_splits=["train"],
    evaluation_splits=["train"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[drupal_bug_fixing_metric],
    generation_size=512,
    stop_sequence=["```", "\n\n"],
    trust_dataset=False,
)

# Export tasks for LightEval discovery
TASKS_TABLE = [
    drupal_quiz_task,
    drupal_code_task,
    drupal_bug_task,
]
