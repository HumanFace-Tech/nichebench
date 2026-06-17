"""LiteLLM judge provider — LLM-as-a-judge for NicheBench.

Package structure
================
``litellm_judge/``
  ``__init__.py``   — public API exports (LiteLLMJudge, _compute_weighted_score)
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
* ``score_quiz`` → ``{"pass", "selected", "score", "explanation", "raw"}``
* ``score_code_generation`` / ``score_bug_fixing`` →
  ``{"criteria", "overall_score", "summary", "raw"}``
* ``score_runtime`` → ``{"criteria", "overall_score", "summary", "raw"}``

Failure handling contract
=========================
* ``score_runtime`` raises ``RuntimeError`` on API/network failure so the
  executor can fall back to deterministic-only scoring.
* All other ``score_*`` methods return a zero-score verdict (no exception)
  on any error path because they are called inside DeepEval metric wrappers
  that manage their own retry budgets.
"""

from .judge import LiteLLMJudge
from .scoring import _compute_weighted_score

__all__ = [
    "LiteLLMJudge",
    "_compute_weighted_score",
]
