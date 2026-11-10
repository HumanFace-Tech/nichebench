"""Tests for the deterministic, network-free quiz scorer."""

from __future__ import annotations

import inspect
import itertools
from pathlib import Path

import pytest

from nichebench.metrics import quiz as quiz_module
from nichebench.metrics.quiz import parse_choice_letter, score_quiz
from nichebench.metrics.types import (
    PARSE_REASON_EMPTY,
    PARSE_REASON_MULTIPLE,
    PARSE_REASON_NO_LETTER,
    PARSE_REASON_OK,
    PARSE_REASON_OUT_OF_RANGE,
    ParseResult,
    QuizVerdict,
    is_parse_failure,
)

NUM_CHOICES = 6
LETTERS = "ABCDEF"

# Wrapper templates that must all resolve to the wrapped letter.
WRAPPER_TEMPLATES = (
    "{}",
    " {} ",
    "{}.",
    "**{}**",
    "({})",
    "The answer is {}",
    "Answer: {}",
    "Final answer: {}",
)


# --- positive parsing -------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "B",
        "b",
        " B ",
        "B)",
        "B.",
        "**B**",
        "`B`",
        '"B"',
    ],
)
def test_parse_single_letter_variants(candidate: str) -> None:
    result = parse_choice_letter(candidate, NUM_CHOICES)
    assert result == ParseResult(letter="B", reason=PARSE_REASON_OK)


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("The answer is B", "B"),
        ("Answer: **B**", "B"),
        ("Option (C) is correct", "C"),
        ("I choose D", "D"),
        ("Final answer: E", "E"),
        ("1. F", "F"),
        ("B) because the hook runs on save", "B"),
        ("A) A) Switch CI to CD", "A"),
        ("Answer: (F)", "F"),
        ("<think>maybe B</think>B", "B"),
    ],
)
def test_parse_explicit_patterns(candidate: str, expected: str) -> None:
    result = parse_choice_letter(candidate, NUM_CHOICES)
    assert result.letter == expected
    assert result.reason == PARSE_REASON_OK


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("B. Because option A is deprecated", "B"),
        ("B, since A is wrong", "B"),
        ("The answer is B because A and C are deprecated", "B"),
        ("Sure! B", "B"),
        ("It is B.", "B"),
        ("I think the answer would be A.", "A"),
        ("Because A is wrong, it is B", "B"),
    ],
)
def test_parse_single_letter_is_authoritative(candidate: str, expected: str) -> None:
    """One explicit answer letter wins over letters mentioned in the text."""
    result = parse_choice_letter(candidate, NUM_CHOICES)
    assert result.letter == expected
    assert result.reason == PARSE_REASON_OK


# --- negative / ambiguous ----------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("B and C", ParseResult(letter=None, reason=PARSE_REASON_MULTIPLE)),
        ("The answer is B and C", ParseResult(letter=None, reason=PARSE_REASON_MULTIPLE)),
        ("B or C", ParseResult(letter=None, reason=PARSE_REASON_MULTIPLE)),
        ("B/C", ParseResult(letter=None, reason=PARSE_REASON_MULTIPLE)),
        ("b and c", ParseResult(letter=None, reason=PARSE_REASON_MULTIPLE)),
        ("The module uses A", ParseResult(letter=None, reason=PARSE_REASON_NO_LETTER)),
        ("", ParseResult(letter=None, reason=PARSE_REASON_EMPTY)),
        ("Z", ParseResult(letter="Z", reason=PARSE_REASON_OUT_OF_RANGE)),
    ],
)
def test_parse_rejects_unlisted_or_ambiguous_answers(
    candidate: str, expected: ParseResult
) -> None:
    result = parse_choice_letter(candidate, NUM_CHOICES)
    assert result == expected


@pytest.mark.parametrize("candidate", ["", "   ", "\n\t "])
def test_parse_empty_answer(candidate: str) -> None:
    result = parse_choice_letter(candidate, NUM_CHOICES)
    assert result.letter is None
    assert result.reason == PARSE_REASON_EMPTY


@pytest.mark.parametrize("candidate", ["Z", "G"])
def test_parse_out_of_range_letter(candidate: str) -> None:
    result = parse_choice_letter(candidate, NUM_CHOICES)
    assert result.reason == PARSE_REASON_OUT_OF_RANGE
    assert result.letter == candidate


def test_parse_multiple_letters() -> None:
    result = parse_choice_letter("B and C", NUM_CHOICES)
    assert result.letter is None
    assert result.reason == PARSE_REASON_MULTIPLE


@pytest.mark.parametrize(
    "candidate",
    ["The module uses A", "I don't know", "None of these", "not sure", "The answer is a good one"],
)
def test_parse_no_letter(candidate: str) -> None:
    result = parse_choice_letter(candidate, NUM_CHOICES)
    assert result.letter is None
    assert result.reason == PARSE_REASON_NO_LETTER


def test_parse_ignores_think_blocks() -> None:
    result = parse_choice_letter("<think>maybe B</think>B", NUM_CHOICES)
    assert result == ParseResult(letter="B", reason=PARSE_REASON_OK)


def test_parse_accepts_lower_case_answer_letter() -> None:
    result = parse_choice_letter("The answer is b", NUM_CHOICES)
    assert result == ParseResult(letter="B", reason=PARSE_REASON_OK)


# --- range handling ----------------------------------------------------------


def test_letter_above_choice_count_is_out_of_range() -> None:
    result = parse_choice_letter("E", 4)
    assert result.reason == PARSE_REASON_OUT_OF_RANGE


def test_letter_within_choice_count_is_accepted() -> None:
    result = parse_choice_letter("F", 6)
    assert result == ParseResult(letter="F", reason=PARSE_REASON_OK)


def test_num_choices_must_be_at_least_two() -> None:
    with pytest.raises(ValueError):
        parse_choice_letter("A", 1)


# --- scoring -----------------------------------------------------------------


def test_score_quiz_pass() -> None:
    verdict = score_quiz("B", "B", NUM_CHOICES)
    assert verdict.passed is True
    assert verdict.score == 1.0
    assert verdict.selected == "B"
    assert verdict.gold == "B"
    assert verdict.method == "deterministic"
    assert verdict.parse_reason == PARSE_REASON_OK
    assert verdict.explanation == "Selected B; gold B"


def test_score_quiz_wrong_choice() -> None:
    verdict = score_quiz("C", "B", NUM_CHOICES)
    assert verdict.passed is False
    assert verdict.score == 0.0
    assert verdict.selected == "C"
    assert verdict.explanation == "Selected C; gold B"


def test_score_quiz_unparseable_is_a_failure_with_reason() -> None:
    verdict = score_quiz("I don't know", "B", NUM_CHOICES)
    assert verdict.passed is False
    assert verdict.score == 0.0
    assert verdict.selected is None
    assert verdict.parse_reason == PARSE_REASON_NO_LETTER
    assert "no_letter" in verdict.explanation


def test_score_quiz_empty_answer_explanation() -> None:
    verdict = score_quiz("", "B", NUM_CHOICES)
    assert verdict.parse_reason == PARSE_REASON_EMPTY
    assert verdict.explanation == "No parseable letter (empty)"


def test_score_quiz_ambiguous_explanation() -> None:
    verdict = score_quiz("B and C", "B", NUM_CHOICES)
    assert verdict.parse_reason == PARSE_REASON_MULTIPLE
    assert "B" in verdict.explanation and "C" in verdict.explanation


def test_score_quiz_out_of_range_explanation() -> None:
    verdict = score_quiz("Z", "B", NUM_CHOICES)
    assert verdict.passed is False
    assert verdict.parse_reason == PARSE_REASON_OUT_OF_RANGE
    assert "out_of_range" in verdict.explanation


def test_gold_is_normalized_to_uppercase() -> None:
    verdict = score_quiz("b", "b", NUM_CHOICES)
    assert verdict.gold == "B"
    assert verdict.passed is True


def test_invalid_gold_raises_value_error() -> None:
    with pytest.raises(ValueError):
        score_quiz("A", "G", 6)


def test_invalid_gold_for_choice_count_raises_value_error() -> None:
    with pytest.raises(ValueError):
        score_quiz("A", "A", 1)


def test_invalid_gold_shape_raises_value_error() -> None:
    with pytest.raises(ValueError):
        score_quiz("A", "BB", 6)


# --- determinism and isolation ----------------------------------------------


def test_scoring_is_deterministic() -> None:
    first = score_quiz("The answer is B", "B", NUM_CHOICES)
    second = score_quiz("The answer is B", "B", NUM_CHOICES)
    assert first == second


def test_scoring_never_calls_the_llm_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from nichebench.providers.litellm_client import LiteLLMClient

    def explode(*args: object, **kwargs: object) -> dict[str, str]:
        raise AssertionError("the deterministic quiz scorer must not call an LLM")

    monkeypatch.setattr(LiteLLMClient, "generate", explode)
    monkeypatch.setattr(LiteLLMClient, "generate_with_messages", explode)

    verdict = score_quiz("B", "B", NUM_CHOICES)
    assert verdict.passed is True


def test_quiz_module_has_no_llm_or_deepeval_dependency() -> None:
    source = Path(inspect.getfile(quiz_module)).read_text(encoding="utf-8")
    assert "litellm" not in source.lower()
    assert "deepeval" not in source.lower()
    assert "LiteLLMClient" not in source


# --- parse-failure taxonomy --------------------------------------------------


def test_parse_failure_taxonomy() -> None:
    assert is_parse_failure(PARSE_REASON_EMPTY)
    assert is_parse_failure(PARSE_REASON_NO_LETTER)
    assert is_parse_failure(PARSE_REASON_MULTIPLE)
    assert is_parse_failure(PARSE_REASON_OUT_OF_RANGE)
    assert not is_parse_failure(PARSE_REASON_OK)


def test_verdict_defaults_are_deterministic() -> None:
    verdict = QuizVerdict(
        selected="B",
        gold="B",
        passed=True,
        score=1.0,
        explanation="Selected B; gold B",
    )
    assert verdict.method == "deterministic"
    assert verdict.parse_reason == PARSE_REASON_OK


# --- property style checks ---------------------------------------------------


@pytest.mark.parametrize("letter", LETTERS)
@pytest.mark.parametrize("template", WRAPPER_TEMPLATES)
def test_every_letter_survives_every_wrapper(letter: str, template: str) -> None:
    result = parse_choice_letter(template.format(letter), NUM_CHOICES)
    assert result.letter == letter
    assert result.reason == PARSE_REASON_OK


@pytest.mark.parametrize(("first", "second"), list(itertools.combinations(LETTERS, 2)))
def test_two_letters_are_ambiguous(first: str, second: str) -> None:
    result = parse_choice_letter(f"{first} and {second}", NUM_CHOICES)
    assert result.letter is None
    assert result.reason == PARSE_REASON_MULTIPLE
