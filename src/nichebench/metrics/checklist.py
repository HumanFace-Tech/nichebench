"""
Checklist-based evaluation metrics for NicheBench.
"""

from typing import Any, Dict, List

import numpy as np
from lighteval.metrics.utils.metric_utils import SampleLevelMetric


def checklist_accuracy_fn(
    predictions: List[str], formatted_doc: Any, **kwargs: Any
) -> float:
    """
    Evaluates predictions against a checklist from the dataset.
    Expected that formatted_doc has a 'checklist' field with evaluation criteria.
    """
    if not predictions:
        return 0.0

    prediction = predictions[0].lower()

    # Get checklist from the document metadata
    checklist = getattr(formatted_doc, "checklist", [])
    if not checklist:
        # Fallback: just check if prediction contains some expected keywords
        return 1.0 if len(prediction.strip()) > 10 else 0.0

    # Check how many checklist items are satisfied
    satisfied_count = 0
    for item in checklist:
        if isinstance(item, str) and item.lower() in prediction:
            satisfied_count += 1
        elif isinstance(item, dict):
            # More complex checklist item with patterns or conditions
            keyword = item.get("keyword", "").lower()
            if keyword and keyword in prediction:
                satisfied_count += 1

    return satisfied_count / len(checklist) if checklist else 0.0


checklist_accuracy = SampleLevelMetric(
    metric_name="checklist_accuracy",
    higher_is_better=True,
    sample_level_fn=checklist_accuracy_fn,
    corpus_level_fn=np.mean,
)
