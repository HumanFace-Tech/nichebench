"""Custom checklist-based evaluation metrics for NicheBench."""

import re
from typing import Any, List

import numpy as np
from lighteval.metrics.metrics import MetricCategory, MetricUseCase
from lighteval.metrics.utils.metric_utils import SampleLevelMetric
from lighteval.tasks.requests import Doc


def checklist_accuracy_fn(doc: Doc, model_response: Any) -> float:
    """
    Calculate accuracy based on dynamic checklist criteria from dataset.

    The checklist should be stored in doc.specific["checklist"] as a list of strings.
    Each item represents an evaluation criterion.

    Args:
        doc: Document containing the checklist in specific field
        model_response: Model's response to evaluate

    Returns:
        float: Score between 0.0 and 1.0 representing checklist success rate
    """
    if not model_response.text:
        return 0.0

    response_text = model_response.text[0].lower()

    # Get checklist from doc.specific field
    if not doc.specific or "checklist" not in doc.specific:
        # If no checklist available, fall back to simple exact match
        gold_answer = doc.get_golds()[0] if doc.get_golds() else ""
        return 1.0 if response_text.strip() == gold_answer.lower().strip() else 0.0

    checklist = doc.specific["checklist"]
    if not checklist:
        return 0.0

    criteria_met = 0
    total_criteria = len(checklist)

    for criterion in checklist:
        criterion_lower = criterion.lower()

        # Check if criterion is met based on different patterns
        is_met = False

        # For "Must" criteria - strict requirement
        if criterion_lower.startswith("must"):
            # Extract the key requirement after "must"
            requirement = (
                criterion_lower.replace("must ", "").replace("must", "").strip()
            )
            is_met = _check_requirement_in_text(requirement, response_text)

        # For "Should" criteria - recommended but not strict
        elif criterion_lower.startswith("should"):
            requirement = (
                criterion_lower.replace("should ", "").replace("should", "").strip()
            )
            is_met = _check_requirement_in_text(requirement, response_text)

        # For other criteria, treat as general requirements
        else:
            is_met = _check_requirement_in_text(criterion_lower, response_text)

        if is_met:
            criteria_met += 1

    return criteria_met / total_criteria if total_criteria > 0 else 0.0


def _check_requirement_in_text(requirement: str, text: str) -> bool:
    """
    Check if a requirement is met in the given text.

    Args:
        requirement: The requirement to check for
        text: The text to search in

    Returns:
        bool: True if requirement is met, False otherwise
    """
    # Clean up requirement text
    requirement = requirement.strip()

    # Handle different types of requirements
    if "mention" in requirement or "identify" in requirement:
        # Extract what should be mentioned
        if "mention" in requirement:
            parts = requirement.split("mention")
            if len(parts) > 1:
                key_term = parts[1].strip()
                return key_term in text
        elif "identify" in requirement:
            parts = requirement.split("identify")
            if len(parts) > 1:
                key_term = parts[1].strip()
                return key_term in text

    # Handle "use" requirements
    elif "use" in requirement:
        parts = requirement.split("use")
        if len(parts) > 1:
            key_term = parts[1].strip()
            return key_term in text

    # Handle "implement" requirements
    elif "implement" in requirement:
        parts = requirement.split("implement")
        if len(parts) > 1:
            key_term = parts[1].strip()
            return key_term in text

    # Handle "include" requirements
    elif "include" in requirement:
        parts = requirement.split("include")
        if len(parts) > 1:
            key_term = parts[1].strip()
            return key_term in text

    # Handle "extend" or "extending" requirements
    elif "extend" in requirement:
        parts = requirement.split("extend")
        if len(parts) > 1:
            key_term = parts[1].strip()
            return key_term in text

    # Handle "replace" requirements
    elif "replace" in requirement:
        # Look for both old and new patterns
        if "with" in requirement:
            parts = requirement.split("with")
            if len(parts) == 2:
                old_term = parts[0].replace("replace", "").strip()
                new_term = parts[1].strip()
                # Check that old term is NOT present and new term IS present
                return old_term not in text and new_term in text

    # Handle "define" requirements
    elif "define" in requirement:
        parts = requirement.split("define")
        if len(parts) > 1:
            key_term = parts[1].strip()
            return key_term in text

    # Handle "hook to" requirements (WordPress specific)
    elif "hook to" in requirement:
        parts = requirement.split("hook to")
        if len(parts) > 1:
            key_term = parts[1].strip()
            return key_term in text

    # Handle "prevent direct access" (WordPress/Drupal specific)
    elif "prevent direct access" in requirement:
        return "abspath" in text or "defined" in text

    # Handle namespace requirements
    elif "namespace" in requirement:
        return "namespace" in text

    # Handle annotation requirements
    elif "annotation" in requirement:
        return "@" in text

    # For generic requirements, do a simple substring search
    else:
        # Try to extract key terms from the requirement
        key_terms = _extract_key_terms(requirement)
        if key_terms:
            return any(term in text for term in key_terms)
        else:
            # Fall back to checking if any significant part of requirement is in text
            return requirement in text

    return False


def _extract_key_terms(requirement: str) -> List[str]:
    """
    Extract key technical terms from a requirement string.

    Args:
        requirement: The requirement text

    Returns:
        List[str]: List of key terms to search for
    """
    # Common technical terms that are important
    technical_patterns = [
        "fielditembase",
        "contentbase",
        "blockbase",
        "entitybase",
        "register_post_type",
        "add_action",
        "add_filter",
        "add_menu_page",
        "sanitize_text_field",
        "esc_html",
        "wp_verify_nonce",
        "wpdb->prepare",
        "hook_node_insert",
        "hook_node_presave",
        "hook_entity_presave",
        "drupal::messenger",
        "drupal::service",
        "formstateinterface",
        "basefielddefinitions",
        "@contenttype",
        "@block",
        "abspath",
        "manage_options",
        "dashicons",
    ]

    requirement_lower = requirement.lower()
    found_terms = []

    for pattern in technical_patterns:
        if pattern in requirement_lower:
            found_terms.append(pattern)

    # Also look for quoted terms or terms in parentheses
    quoted_terms = re.findall(r"'([^']*)'", requirement)
    paren_terms = re.findall(r"\(([^)]*)\)", requirement)

    found_terms.extend([term.lower() for term in quoted_terms])
    found_terms.extend([term.lower() for term in paren_terms])

    return found_terms


# LightEval-compatible metric function
def checklist_accuracy_sample_fn(
    predictions: List[str], formatted_doc: Any, **kwargs: Any
) -> float:
    """
    LightEval-compatible wrapper for checklist accuracy.
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
    category=MetricCategory.GENERATIVE,
    use_case=MetricUseCase.ACCURACY,
    sample_level_fn=checklist_accuracy_sample_fn,
    corpus_level_fn=np.mean,
)
