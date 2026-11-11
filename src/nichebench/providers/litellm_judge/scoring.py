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
* Every manifest criterion is scored **at most once**: each checklist item
  resolves to a single criterion — the first not-yet-used one whose
  ``criterion_id`` matches the item id, falling back to the criterion in
  the same position when that entry does not belong to another manifest
  item.  Duplicate or extra criteria are ignored and can never add weight
  twice (a repeated ``criterion_id`` cannot inflate the score).
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

    Every manifest criterion is scored **at most once**.  Each checklist
    item resolves to a single criterion: the first not-yet-used criterion
    whose ``criterion_id`` equals the item id, falling back to the
    criterion in the same position when that entry does not carry an id
    that belongs to another manifest item.  Duplicate and extra criteria
    are ignored, so a repeated ``criterion_id`` never adds weight twice.

    Weights are normalised to sum to 1.0 before scoring.

    The helper is tolerant of malformed input: a checklist entry that is not
    a mapping is ignored (it contributes no weight), a criterion that is not
    a mapping is never matched, and missing ``id`` / ``weight`` / ``pass`` keys
    fall back to their documented defaults.  Malformed *scored* criteria are
    already rejected at the judge boundary — this tolerance only keeps a
    malformed verdict from raising instead of being scored.

    Args:
        criteria: list of criterion dicts returned by the judge LLM, each
            containing ``criterion_id`` and ``pass`` keys.
        checklist_items: list of manifest checklist dicts, each containing
            ``id`` and ``weight`` keys.

    Returns:
        A float in ``[0.0, 1.0]``.
    """
    weight_by_id = {
        str(item.get("id", "")): _safe_weight(item.get("weight", 1.0))
        for item in checklist_items
        if isinstance(item, dict)
    }
    total_weight = sum(weight_by_id.values())
    if total_weight <= 0 or not criteria:
        return 0.0

    score = 0.0
    used: set[int] = set()

    for position, item in enumerate(checklist_items):
        if not isinstance(item, dict):
            continue
        weight = _safe_weight(item.get("weight", 1.0))
        item_id = str(item.get("id", ""))

        # Primary: the first not-yet-used criterion carrying this id.
        chosen: int | None = None
        for index, candidate in enumerate(criteria):
            if index in used or not isinstance(candidate, dict):
                continue
            if str(candidate.get("criterion_id", "")) == item_id:
                chosen = index
                break

        # Fallback: the criterion at the same position, but only when it
        # does not belong to another manifest item — a duplicate criterion
        # for a different id is never credited here.
        if chosen is None and position < len(criteria):
            candidate = criteria[position]
            if (
                position not in used
                and isinstance(candidate, dict)
                and str(candidate.get("criterion_id", "")) not in weight_by_id
            ):
                chosen = position

        if chosen is None:
            continue
        used.add(chosen)

        criterion = criteria[chosen]
        pass_val = criterion.get("pass", False)
        if pass_val is True:
            score += weight
        elif pass_val == "partial":
            score += weight * 0.5

    return max(0.0, min(1.0, score / total_weight))
