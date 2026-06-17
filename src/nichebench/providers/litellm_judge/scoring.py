"""Weighted scoring helpers for the judge.

This module provides :func:`_compute_weighted_score`, which resolves
per-criterion ``pass`` values from the judge LLM against the manifest's
``weight`` field.

Ownership
=========
This module is owned by the ``litellm_judge`` package. It is called
exclusively by ``judge.py`` inside the ``score_runtime`` method.

Scoring contract
================
* Criteria are matched to checklist items by ``criterion_id`` (primary)
  or by position (fallback, when fewer than half the IDs are recognised).
* Negative weights are clamped to ``0.0`` so a malformed manifest cannot
  produce a negative ``overall_score``.  The final result is clamped to
  ``[0.0, 1.0]``.
* Weights are normalised to sum to 1.0 before scoring.
* ``pass == True`` → full weight; ``pass == "partial"`` → 50% of weight;
  ``pass == False`` → 0.
* Result is clamped to ``[0.0, 1.0]``.
"""

from typing import Any


def _safe_weight(raw: Any) -> float:
    """Coerce a manifest weight to a non-negative float, defaulting to 1.0.

    Negative or non-numeric weights are treated as ``0.0`` so a malformed
    manifest cannot produce a negative overall score.
    """
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if value < 0.0:
        return 0.0
    return value


def _compute_weighted_score(
    criteria: list[dict[str, Any]],
    checklist_items: list[dict[str, Any]],
) -> float:
    """Compute weighted score from judge criteria, matched by criterion_id.

    Falls back to positional matching if fewer than half of criteria IDs are
    recognised. Weights are normalised to sum to 1.0 before scoring.

    Args:
        criteria: list of criterion dicts returned by the judge LLM, each
            containing ``criterion_id`` and ``pass`` keys.
        checklist_items: list of manifest checklist dicts, each containing
            ``id`` and ``weight`` keys.

    Returns:
        A float in ``[0.0, 1.0]``.
    """
    weight_by_id = {str(item.get("id", "")): _safe_weight(item.get("weight", 1.0)) for item in checklist_items}
    total_weight = sum(weight_by_id.values())
    if total_weight <= 0 or not criteria:
        return 0.0

    # Try ID-based matching first
    matched_ids: set[str] = set()
    score = 0.0
    for c in criteria:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("criterion_id", ""))
        if cid in weight_by_id:
            matched_ids.add(cid)
            pass_val = c.get("pass", False)
            if pass_val is True:
                score += weight_by_id[cid]
            elif pass_val == "partial":
                score += weight_by_id[cid] * 0.5

    # If we matched at least half the items by ID, trust the ID-based result
    if len(matched_ids) >= len(checklist_items) / 2:
        return max(0.0, min(1.0, score / total_weight))

    # Positional fallback: assume criteria are in the same order as checklist_items
    score = 0.0
    for i, c in enumerate(criteria):
        if i >= len(checklist_items):
            break
        if not isinstance(c, dict):
            continue
        weight = _safe_weight(checklist_items[i].get("weight", 1.0))
        pass_val = c.get("pass", False)
        if pass_val is True:
            score += weight
        elif pass_val == "partial":
            score += weight * 0.5
    return max(0.0, min(1.0, score / total_weight))
