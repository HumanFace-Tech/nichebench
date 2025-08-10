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
    if not predictions or not getattr(formatted_doc, "choices", None):
        return 0.0
    pred_raw = (predictions[0] or "").strip().upper()
    # Extract a single letter A-E from the prediction
    import re

    m = re.search(r"[A-E]", pred_raw)
    pred_letter = m.group(0) if m else ""

    # Gold is at gold_index within choices; normalize choice label to its letter
    try:
        gold_choice = formatted_doc.choices[formatted_doc.gold_index]
    except Exception:
        return 0.0
    gold_str = str(gold_choice).strip().upper()
    m2 = re.match(r"^([A-E])\)", gold_str)
    gold_letter = m2.group(1) if m2 else gold_str[:1] if gold_str[:1] in "ABCDE" else ""
    return 1.0 if pred_letter and pred_letter == gold_letter else 0.0


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
# Note: For quizzes we prefer LightEval's built-in loglikelihood-based accuracy,
# which avoids parsing generated answers and yields a boolean correctness.

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


# Locate YAML data directory (Drupal)
_DRUPAL_DATA_DIR = Path(__file__).parent / "data"


# Simplified prompt function wrappers - these just delegate to the real implementations
def drupal_quiz_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for quiz data - delegates to actual implementation."""
    from .prompt_functions import drupal_quiz_prompt

    return drupal_quiz_prompt(line, task_name)


def drupal_code_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for code generation data - delegates to actual implementation."""
    from .prompt_functions import drupal_code_generation_prompt

    return drupal_code_generation_prompt(line, task_name)


def drupal_bug_hardcoded_prompt(line: Dict[str, Any], task_name: str = None) -> Doc:
    """Prompt function for bug fixing data - delegates to actual implementation."""
    from .prompt_functions import drupal_bug_fixing_prompt

    return drupal_bug_fixing_prompt(line, task_name)


# Task configurations using hardcoded data
drupal_quiz_task = LightevalTaskConfig(
    name="nichebench_drupal_quiz",
    prompt_function=drupal_quiz_hardcoded_prompt,
    suite=["community"],
    hf_repo="local",  # Use local hardcoded data
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[Metrics.loglikelihood_acc],
    generation_size=-1,  # not generating; using loglikelihood on choices
    stop_sequence=None,
    trust_dataset=True,
)

drupal_code_task = LightevalTaskConfig(
    name="nichebench_drupal_code_generation",
    prompt_function=drupal_code_hardcoded_prompt,
    suite=["community"],
    hf_repo="local",  # Use local hardcoded data
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[drupal_code_metric],
    generation_size=512,
    stop_sequence=["```", "\n\n"],
    trust_dataset=True,
)

drupal_bug_task = LightevalTaskConfig(
    name="nichebench_drupal_bug_fixing",
    prompt_function=drupal_bug_hardcoded_prompt,
    suite=["community"],
    hf_repo="local",  # Use local hardcoded data
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[drupal_bug_fixing_metric],
    generation_size=512,
    stop_sequence=["```", "\n\n"],
    trust_dataset=True,
)

# Export tasks for LightEval discovery
TASKS_TABLE = [
    drupal_quiz_task,
    drupal_code_task,
    drupal_bug_task,
]
