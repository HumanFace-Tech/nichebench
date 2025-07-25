"""
WordPress tasks for NicheBench using LightEval configuration.
"""

from typing import Any, List

import numpy as np
from lighteval.metrics.metrics import MetricCategory, Metrics, MetricUseCase
from lighteval.metrics.utils.metric_utils import SampleLevelMetric
from lighteval.tasks.lighteval_task import LightevalTaskConfig

from .prompt_functions import (
    wordpress_plugin_generation_prompt,
    wordpress_plugin_quiz_prompt,
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
    metric_name="wp_quiz_accuracy",
    higher_is_better=True,
    category=MetricCategory.GENERATIVE,
    use_case=MetricUseCase.ACCURACY,
    sample_level_fn=quiz_accuracy_fn,
    corpus_level_fn=np.mean,
)


# Plugin code evaluation metric
def plugin_quality_fn(
    predictions: List[str], formatted_doc: Any, **kwargs: Any
) -> float:
    """Basic WordPress plugin code quality evaluation."""
    if not predictions:
        return 0.0

    code = predictions[0].lower()
    # Basic WordPress-specific checks
    has_wp_functions = any(
        wp_func in code for wp_func in ["add_action", "add_filter", "wp_enqueue"]
    )
    has_plugin_structure = "<?php" in code
    has_hooks = "hook" in code or "action" in code or "filter" in code

    score = 0.0
    if has_plugin_structure:
        score += 0.4
    if has_wp_functions:
        score += 0.4
    if has_hooks:
        score += 0.2

    return score


plugin_quality = SampleLevelMetric(
    metric_name="plugin_quality",
    higher_is_better=True,
    category=MetricCategory.GENERATIVE,
    use_case=MetricUseCase.CODE,
    sample_level_fn=plugin_quality_fn,
    corpus_level_fn=np.mean,
)

# Task Configurations
wordpress_quiz_task = LightevalTaskConfig(
    name="nichebench_wordpress_quiz",
    prompt_function=wordpress_plugin_quiz_prompt,
    suite=["community"],
    hf_repo="nichebench/wordpress-quiz-v1",  # Placeholder
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[Metrics.loglikelihood_acc, quiz_accuracy],
    generation_size=50,
    stop_sequence=None,
)

wordpress_plugin_gen_task = LightevalTaskConfig(
    name="nichebench_wordpress_plugin_generation",
    prompt_function=wordpress_plugin_generation_prompt,
    suite=["community"],
    hf_repo="nichebench/wordpress-plugin-gen-v1",  # Placeholder
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metric=[Metrics.exact_match, plugin_quality],
    generation_size=512,
    stop_sequence=["```", "\n\n"],
)

# Store tasks for LightEval discovery
TASKS_TABLE = [
    wordpress_quiz_task,
    wordpress_plugin_gen_task,
]
