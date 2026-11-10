"""LiteLLM judge provider — LLM-as-a-judge for NicheBench.

Package structure
================
``litellm_judge/``
  ``__init__.py``   — public API exports (JudgeError, LiteLLMJudge, _compute_weighted_score)
  ``judge.py``      — LiteLLMJudge facade with score_* methods
  ``scoring.py``    — _compute_weighted_score helper
  ``parsing.py``    — parse_json_safe wrapper
  ``categories.py`` — build_*_prompt helpers for each task category

Public API
==========
All external callers should import from this package root only::

    from nichebench.providers.litellm_judge import LiteLLMJudge

Do not import directly from sub-modules (``judge.py``, ``scoring.py``, etc.)
as those are internal implementation details.

Score_* output shapes
====================
All ``score_*`` methods return a dict with at least:

* ``raw`` — the unmodified judge output string (for debugging)
* ``pass`` or ``overall_score`` — the primary score signal

Exact shapes:
* ``score_quiz`` → ``{"pass", "selected", "score", "overall_score",
  "explanation", "judge_pass", "judge_pass_mismatch", "attempts", "raw"}``
  (raises ``JudgeError`` when unusable)
* ``score_code_generation`` / ``score_bug_fixing`` →
  ``{"criteria", "overall_score", "judge_overall_score", "summary",
  "attempts", "raw"}`` (raises ``JudgeError`` when unusable)
* ``score_runtime`` → ``{"criteria", "overall_score", "summary", "raw"}``

Failure handling contract
=========================
* ``score_runtime`` raises ``RuntimeError`` on API/network failure so the
  executor can fall back to deterministic-only scoring.
* ``score_code_generation`` / ``score_bug_fixing`` / ``score_quiz`` retry
  unusable judge output (``[Error: ...]``, empty/non-JSON, missing
  criteria / missing ``selected``) up to ``max_attempts`` times, then
  raise ``JudgeError`` carrying the error ``kind`` and attempt count — the
  runner maps it to an ``error_kind`` verdict instead of a fabricated
  zero score.
"""

from .judge import JudgeError, LiteLLMJudge
from .scoring import _compute_weighted_score

__all__ = [
    "JudgeError",
    "LiteLLMJudge",
    "_compute_weighted_score",
]
