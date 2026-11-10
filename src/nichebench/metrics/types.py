"""Shared result types for scoring verdicts.

Deliberately dependency-free (standard library only) so the scoring path
can never pull in the LLM client or anything network capable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# --- Judge error taxonomy ---------------------------------------------------
# Kinds carried by ``MetricVerdict.error_kind`` / ``TestResult.error_kind``
# when the judge could not produce a usable verdict.  Judge errors describe
# infrastructure problems, not model failures.

JUDGE_ERROR_UNAVAILABLE: Final[str] = "judge_unavailable"
JUDGE_ERROR_INVALID_JSON: Final[str] = "judge_invalid_json"
JUDGE_ERROR_MISSING_CRITERIA: Final[str] = "judge_missing_criteria"
JUDGE_ERROR_OUT_OF_RANGE: Final[str] = "judge_out_of_range"
JUDGE_ERROR_MISSING_SELECTED: Final[str] = "judge_missing_selected"
JUDGE_ERROR_CONFIG: Final[str] = "config_error"

# --- Parse-failure taxonomy -------------------------------------------------
# Every parse outcome carries exactly one of these reasons.  ``ok`` is the only
# non-failure reason; the rest describe why no single choice letter could be
# determined from the model answer.

PARSE_REASON_OK: Final[str] = "ok"
PARSE_REASON_EMPTY: Final[str] = "empty"
PARSE_REASON_NO_LETTER: Final[str] = "no_letter"
PARSE_REASON_MULTIPLE: Final[str] = "multiple_letters"
PARSE_REASON_OUT_OF_RANGE: Final[str] = "out_of_range"

PARSE_REASONS: Final[tuple[str, ...]] = (
    PARSE_REASON_OK,
    PARSE_REASON_EMPTY,
    PARSE_REASON_NO_LETTER,
    PARSE_REASON_MULTIPLE,
    PARSE_REASON_OUT_OF_RANGE,
)

PARSE_FAILURE_REASONS: Final[tuple[str, ...]] = tuple(
    reason for reason in PARSE_REASONS if reason != PARSE_REASON_OK
)


@dataclass(frozen=True)
class ParseResult:
    """Outcome of parsing a free-form answer into a choice letter.

    ``letter`` holds the detected letter (uppercase) when one could be
    determined, including letters that are out of range.  It is ``None`` when
    nothing could be parsed (``empty``, ``no_letter``) or when several
    candidates were found (``multiple_letters``).
    """

    letter: str | None
    reason: str


@dataclass(frozen=True)
class QuizVerdict:
    """Deterministic verdict for a single multiple-choice question."""

    selected: str | None
    gold: str
    passed: bool
    score: float
    explanation: str
    method: str = "deterministic"
    parse_reason: str = PARSE_REASON_OK


def is_parse_failure(reason: str) -> bool:
    """Return ``True`` when ``reason`` denotes a failed parse."""
    return reason in PARSE_FAILURE_REASONS


@dataclass(frozen=True)
class MetricVerdict:
    """Verdict produced by an LLM-judged metric (code generation, bug fixing).

    ``score`` and ``final_score``-style consumers read ``score``;
    ``judge_overall_score`` keeps the raw ``overall_score`` the judge
    reported.  ``error_kind`` is set (and ``score`` is ``None``) when the
    judge could not produce a usable verdict — see the ``JUDGE_ERROR_*``
    constants.
    """

    score: float | None
    passed: bool
    criteria: list[dict]
    judge_overall_score: float | None
    summary: str
    method: str = "llm"
    error_kind: str | None = None
    attempts: int = 1


@dataclass(frozen=True)
class SanityFinding:
    """Structural sanity observation about a model output.

    ``severity`` is advisory (``"info"`` / ``"warn"``) or hard-fail
    (``"error"``) per the v2 plan; ``evidence`` carries the snippet or
    offset that triggered the check.
    """

    check: str
    severity: str
    message: str
    evidence: str = ""
