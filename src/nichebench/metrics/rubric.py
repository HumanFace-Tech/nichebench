"""Score recomputation for rubric-based (checklist) judge verdicts.

The judge LLM reports its own ``overall_score`` alongside per-criterion
``pass`` values, but that self-reported number is **audit-only**: it is
preserved under ``judge_overall_score`` for forensics and never trusted
as the result score.  The authoritative score is always recomputed from
the criteria with :func:`criteria_to_score`, so a judge claiming a high
(or out-of-range) score while marking every criterion as failed cannot
inflate the outcome.

Standard library only — this module sits in the scoring path.
"""

from __future__ import annotations

import math
from typing import Any, Optional


def normalize_score(value: Any) -> Optional[float]:
    """Return ``value`` as a float clamped to ``[0, 1]``, or ``None`` when invalid.

    Booleans, non-numeric values (including numeric strings such as
    ``"0.8"``) and non-finite floats (``NaN`` / ``±inf``) are rejected.
    Finite numbers outside ``[0, 1]`` (e.g. ``1.2``, ``-0.2``) are
    clamped into range rather than rejected.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score):
        return None
    return max(0.0, min(1.0, score))


def criteria_to_score(criteria: Any, checklist: list[str]) -> Optional[float]:
    """Recompute a rubric score from the judge's criteria, positionally.

    Criteria are matched to the checklist **by position**: slot ``i`` of
    ``criteria`` is scored against ``checklist[i]`` regardless of the
    criterion text.  ``True`` counts ``1.0``, ``"partial"`` counts
    ``0.5``, anything else — including a missing criterion — counts
    ``0.0`` (conservative).  Criteria beyond ``len(checklist)`` are
    ignored, and the result is normalized by the checklist length, never
    by the number of returned criteria.

    Returns ``None`` when ``checklist`` is empty (nothing to score
    against) or when ``criteria`` is not a list — callers treat ``None``
    as "no usable verdict" and retry or raise rather than scoring.
    """
    if not checklist:
        return None
    if not isinstance(criteria, list):
        return None
    total = 0.0
    for index in range(len(checklist)):
        if index >= len(criteria):
            continue
        item = criteria[index]
        pass_value = item.get("pass") if isinstance(item, dict) else None
        if pass_value is True:
            total += 1.0
        elif pass_value == "partial":
            total += 0.5
    return total / len(checklist)
