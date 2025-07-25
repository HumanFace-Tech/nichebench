"""
Drupal tasks for NicheBench using LightEval configuration.
"""

from typing import Any, List

import numpy as np
from lighteval.metrics import Metrics
from lighteval.metrics.utils.metric_utils import SampleLevelMetric
from lighteval.tasks.lighteval_task import LightevalTaskConfig

from .prompt_functions import (
    drupal_bug_fixing_prompt,
    drupal_code_generation_prompt,
    drupal_quiz_prompt,
)


# Simple quiz accuracy metric
def quiz_accuracy_fn(
    predictions: List[str], formatted_doc: Any, **kwargs: Any
) -> float:
    """Simple accuracy check for quiz answers."""
    if not predictions:
        return 0.0

    prediction = predictions[0].strip().upper()
    correct_answer = formatted_doc.choices[formatted_doc.gold_index].strip().upper()

    # Extract choice letter (A, B, C, D, E)
    import re

    pred_match = re.search(r"[ABCDE]", prediction)
    pred_choice = pred_match.group(0) if pred_match else ""

    return 1.0 if pred_choice == correct_answer else 0.0


quiz_accuracy = SampleLevelMetric(
    metric_name="quiz_accuracy",
    higher_is_better=True,
    sample_level_fn=quiz_accuracy_fn,
    corpus_level_fn=np.mean,
)


# Code evaluation metric (placeholder for now)
def code_quality_fn(predictions: List[str], formatted_doc: Any, **kwargs: Any) -> float:
    """Placeholder code quality evaluation."""
    # For now, just check if code contains expected patterns
    if not predictions:
        return 0.0

    code = predictions[0].lower()
    # Basic checks - can be enhanced later
    has_function = "function" in code or "def " in code
    has_structure = "{" in code or "class" in code

    return (
        1.0
        if has_function and has_structure
        else 0.5
        if has_function or has_structure
        else 0.0
    )


code_quality = SampleLevelMetric(
    metric_name="code_quality",
    higher_is_better=True,
    sample_level_fn=code_quality_fn,
    corpus_level_fn=np.mean,
)

# Task Configurations
drupal_quiz_task = LightevalTaskConfig(
    name="nichebench_drupal_quiz",
    prompt_function=drupal_quiz_prompt,
    suite=["community"],
    hf_repo="nichebench/drupal-quiz-v1",  # Placeholder - update when dataset is ready
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[Metrics.loglikelihood_acc, quiz_accuracy],  # Use both standard and custom
    generation_size=50,
    stop_sequence=None,
)

drupal_code_gen_task = LightevalTaskConfig(
    name="nichebench_drupal_code_generation",
    prompt_function=drupal_code_generation_prompt,
    suite=["community"],
    hf_repo="nichebench/drupal-code-gen-v1",  # Placeholder
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[Metrics.exact_match, code_quality],  # Use exact match + custom
    generation_size=512,
    stop_sequence=["```", "\n\n"],
)

drupal_bug_fix_task = LightevalTaskConfig(
    name="nichebench_drupal_bug_fixing",
    prompt_function=drupal_bug_fixing_prompt,
    suite=["community"],
    hf_repo="nichebench/drupal-bug-fix-v1",  # Placeholder
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[Metrics.exact_match, code_quality],
    generation_size=512,
    stop_sequence=["```", "\n\n"],
)

# Store tasks for LightEval discovery
TASKS_TABLE = [
    drupal_quiz_task,
    drupal_code_gen_task,
    drupal_bug_fix_task,
]
