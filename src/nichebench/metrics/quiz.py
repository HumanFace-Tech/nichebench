"""Deterministic, network-free scoring for multiple-choice quiz tasks.

The parser is intentionally strict (plan decision D3): an answer is only
accepted when exactly one choice letter can be identified by rule.  Every
failure carries a machine readable ``parse_reason`` so callers can report a
parse-failure taxonomy instead of silently guessing.

Rules, in order:

1. An empty answer is ``empty``.
2. A response that is exactly one letter is that letter (``ok``) or
   ``out_of_range`` when the quiz does not offer it.
3. An explicit answer pattern (``B.``, ``the answer is B``, ``I choose D``,
   ``it is B``, ``1. F``) is authoritative.  Only a *direct list* of answers
   (``B and C``, ``B/C``) next to that letter makes the response ambiguous;
   letters that are merely mentioned in the explanation (``B because A is
   deprecated``) do not.
4. Otherwise the response is scanned for standalone letters.  A single letter
   is accepted when it sits in an answer position (next to an answer cue or
   at the end of a short answer); anything else is ``no_letter``.

This module never imports the LLM client and never performs any I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nichebench.metrics.types import (
    PARSE_REASON_EMPTY,
    PARSE_REASON_MULTIPLE,
    PARSE_REASON_NO_LETTER,
    PARSE_REASON_OK,
    PARSE_REASON_OUT_OF_RANGE,
    ParseResult,
    QuizVerdict,
)

# A quiz needs at least two choices; the letter space is capped at Z.
MIN_NUM_CHOICES = 2
MAX_NUM_CHOICES = 26

# Answers longer than this are only accepted when an explicit pattern matched
# or when the letter is framed by an answer cue or frame word.
MAX_FALLBACK_LENGTH = 40

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_TAIL_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)
_MARKDOWN_RE = re.compile(r"[*`]")
_UNDERSCORE_RE = re.compile(r"_")
_QUOTE_RE = re.compile(r"[\"'“”‘’]")
# Contractions are joined ("it's" -> "its") so the answer frame words can be
# matched on whole tokens.
_CONTRACTION_RE = re.compile(r"(?<=[A-Za-z])'(?=[A-Za-z])")
_SINGLE_LETTER_RE = re.compile(r"[A-Za-z]")
_STANDALONE_UPPER_RE = re.compile(r"\b[A-Z]\b")
_LEADING_TOKEN_RE = re.compile(r"^\(?([A-Za-z])\)?(?![A-Za-z0-9])")

# Explicit answer patterns, tried in this order.  ``[A-Z]`` is matched case
# insensitively, but the trailing ``\b`` keeps it anchored to a standalone
# token so ordinary words ("answer is right") are never mistaken for a choice.
_START_PATTERN = re.compile(r"^\s*\(?([A-Z])\)?\s*(?:[\).:\-]|$)", re.IGNORECASE)
_KEYWORD_PATTERN = re.compile(
    r"\b(?:answer|option|choice|selection)\s*(?:is|:|=)?\s*\(?([A-Z])\)?\b",
    re.IGNORECASE,
)
_CHOICE_PATTERN = re.compile(
    r"\b(?:i\s+(?:choose|pick|select)|final\s+answer|correct\s+answer)\s*(?:is|:)?\s*\(?([A-Z])\)?\b",
    re.IGNORECASE,
)
_NUMBERED_PATTERN = re.compile(r"^\s*\d+[\).\:-]\s*\(?([A-Z])\)?\b", re.IGNORECASE)
# A copula frame: the answer letter follows "is"/"be".  The letter has to come
# after the frame word, so "A is wrong" is not an answer.
_ANSWER_FRAME_PATTERN = re.compile(
    r"\b(?:it is|that is|this is|would be|should be|must be|seems to be)\s+\(?([A-Z])\)?\b",
    re.IGNORECASE,
)

_EXPLICIT_PATTERNS = (
    _START_PATTERN,
    _KEYWORD_PATTERN,
    _CHOICE_PATTERN,
    _ANSWER_FRAME_PATTERN,
    _NUMBERED_PATTERN,
)

# Two letters that are directly listed as the answer(s): "B and C", "B or C",
# "B, C", "B/C".  Letters that only appear in the explanation do not match.
_ANSWER_LIST_RE = re.compile(r"\b([A-Z])\s*(?:and|or|,|/)\s*([A-Z])\b", re.IGNORECASE)

# Words that mark an answer position in free-form text.
_CUE_WORDS = frozenset(
    {
        "answer",
        "option",
        "choice",
        "choose",
        "select",
        "final",
        "correct",
    }
)

# Words that may directly precede the answer letter ("it is B", "sure! B").
# They keep the fallback from reading a sentence that merely mentions a letter
# ("the module uses A") as an answer.
_FRAME_WORDS = frozenset(
    {
        "am",
        "answer",
        "apparently",
        "appear",
        "appears",
        "are",
        "be",
        "been",
        "being",
        "become",
        "becomes",
        "choice",
        "clearly",
        "correct",
        "definitely",
        "final",
        "is",
        "its",
        "likely",
        "look",
        "looks",
        "okay",
        "ok",
        "obviously",
        "option",
        "probably",
        "remain",
        "remains",
        "reply",
        "response",
        "right",
        "seem",
        "seems",
        "sound",
        "sounds",
        "stay",
        "stays",
        "sure",
        "that",
        "was",
        "were",
        "yes",
        "yeah",
        "yep",
    }
)

# Maximum token distance between an answer cue and the answer letter.
_CUE_WINDOW = 5

# Maximum number of tokens that may precede the answer letter in a short
# answer without an answer frame ("Sure! B").
_MAX_LEADING_TOKENS = 2

# Characters stripped from a token before it is compared to a word list.
_TOKEN_PUNCTUATION = ".,;:!?()[]{}\"'`*_"


@dataclass(frozen=True)
class _ParseDetails:
    """Internal parse result that keeps the candidate letters around."""

    letter: str | None
    reason: str
    letters: tuple[str, ...] = ()


def _validate_num_choices(num_choices: int) -> None:
    if not isinstance(num_choices, int) or isinstance(num_choices, bool):
        raise ValueError(f"num_choices must be an int, got {type(num_choices).__name__}")
    if num_choices < MIN_NUM_CHOICES or num_choices > MAX_NUM_CHOICES:
        raise ValueError(
            f"num_choices must be between {MIN_NUM_CHOICES} and {MAX_NUM_CHOICES}, got {num_choices}"
        )


def _allowed_letters(num_choices: int) -> set[str]:
    return {chr(ord("A") + index) for index in range(num_choices)}


def _clean(raw: str) -> str:
    """Normalize a raw model answer into a plain, comparable string."""
    text = raw or ""
    text = _THINK_BLOCK_RE.sub(" ", text)
    text = _THINK_TAIL_RE.sub(" ", text)
    text = _MARKDOWN_RE.sub("", text)
    text = _UNDERSCORE_RE.sub(" ", text)
    text = _CONTRACTION_RE.sub("", text)
    text = _QUOTE_RE.sub(" ", text)
    text = text.strip()
    if text.endswith("."):
        text = text[:-1].strip()
    return text


def _scan_uppercase_letters(text: str, allowed: set[str]) -> set[str]:
    """Standalone uppercase letters of the answer space present in ``text``."""
    return {
        match.group(0) for match in _STANDALONE_UPPER_RE.finditer(text) if match.group(0) in allowed
    }


def _leading_letter(text: str) -> str | None:
    """Return the letter of the first token when that token is a bare letter.

    The original case is preserved so callers can tell "A" (a choice) from
    "a" (the English article).
    """
    tokens = text.split()
    if not tokens:
        return None
    match = _LEADING_TOKEN_RE.match(tokens[0])
    if match is None:
        return None
    return match.group(1)


def _token_words(text: str) -> list[str]:
    """Whitespace tokens with surrounding punctuation removed."""
    return [token.strip(_TOKEN_PUNCTUATION) for token in text.split()]


def _collect_explicit(text: str) -> list[str]:
    """Collect the letter captured by the first explicit pattern that matches.

    The patterns are tried in order and the first one that matches wins: a
    leading ``B.`` is a stronger signal than a later ``option A`` mention.
    Only the first match of that pattern is used.

    A lower-case capture is only accepted when nothing but punctuation follows
    it, so the English article in "the answer is a good one" is not read as
    the choice "a".
    """
    for pattern in _EXPLICIT_PATTERNS:
        for match in pattern.finditer(text):
            captured = match.group(1)
            if captured.islower():
                tail = text[match.end() :].lstrip()
                if tail and tail[0].isalnum():
                    continue
            return [captured.upper()]
    return []


def _listed_letters(text: str, letter: str, allowed: set[str]) -> set[str]:
    """Letters that are directly listed together with ``letter``.

    Only a direct list ("B and C", "B or C", "B, C", "B/C") counts.  A letter
    that is merely mentioned elsewhere in the explanation ("B because A is
    deprecated") does not make the answer ambiguous.
    """
    listed: set[str] = set()
    for match in _ANSWER_LIST_RE.finditer(text):
        first = match.group(1).upper()
        second = match.group(2).upper()
        if first == second:
            continue
        if first not in allowed or second not in allowed:
            continue
        if first == letter or second == letter:
            listed.update((first, second))
    return listed


def _has_nearby_cue(text: str, letter: str) -> bool:
    """Return ``True`` when an answer cue word sits close to ``letter``."""
    words = [word.lower() for word in _token_words(text)]
    index = next((i for i, word in enumerate(words) if word == letter.lower()), None)
    if index is None:
        return False
    return any(
        word in _CUE_WORDS and abs(index - position) <= _CUE_WINDOW
        for position, word in enumerate(words)
    )


def _is_framed_answer(text: str, letter: str) -> bool:
    """Return ``True`` when an answer frame word directly precedes ``letter``.

    "it is B", "it seems to be B" and "sure! B" are answers; "the module
    uses A" is not, because "uses" is no answer frame.
    """
    words = _token_words(text)
    if not words or words[-1].upper() != letter.upper():
        return False
    preceding = words[:-1]
    return bool(preceding) and preceding[-1].lower() in _FRAME_WORDS


def _is_short_filler_answer(text: str, letter: str) -> bool:
    """Return ``True`` for a short answer that is only filler plus the letter.

    "Sure! B" and "It is B" qualify; a sentence that mentions a letter in the
    middle of its content ("the module uses A") does not.
    """
    if len(text) > MAX_FALLBACK_LENGTH:
        return False
    words = _token_words(text)
    if not words or words[-1].upper() != letter.upper():
        return False
    return len(words) - 1 <= _MAX_LEADING_TOKENS


def _is_pronoun_i(leading: str, text: str) -> bool:
    """Return ``True`` for the pronoun "I" ("I don't know")."""
    return leading.upper() == "I" and len(text.split()) > 1


def _fallback_details(cleaned: str, allowed: set[str]) -> _ParseDetails:
    """Parse a response that no explicit pattern matched."""
    # A leading bare letter ("B, since A is wrong") is as strong as an
    # explicit pattern.
    leading = _leading_letter(cleaned)
    if leading is not None:
        letter = leading.upper()
        tokens = cleaned.split()
        is_article = leading == "a" and len(tokens) > 1
        if not is_article and letter in allowed and not _is_pronoun_i(leading, cleaned):
            listed = _listed_letters(cleaned, letter, allowed)
            if listed:
                return _ParseDetails(None, PARSE_REASON_MULTIPLE, tuple(sorted(listed)))
            return _ParseDetails(letter, PARSE_REASON_OK, (letter,))

    letters = _scan_uppercase_letters(cleaned, allowed)
    if not letters:
        return _ParseDetails(None, PARSE_REASON_NO_LETTER)

    if len(letters) > 1:
        return _ParseDetails(None, PARSE_REASON_MULTIPLE, tuple(sorted(letters)))

    letter = next(iter(letters))
    if _has_nearby_cue(cleaned, letter):
        return _ParseDetails(letter, PARSE_REASON_OK, (letter,))
    if _is_framed_answer(cleaned, letter) or _is_short_filler_answer(cleaned, letter):
        return _ParseDetails(letter, PARSE_REASON_OK, (letter,))
    return _ParseDetails(None, PARSE_REASON_NO_LETTER)


def _parse_details(candidate: str, num_choices: int) -> _ParseDetails:
    _validate_num_choices(num_choices)
    allowed = _allowed_letters(num_choices)
    cleaned = _clean(candidate)

    if not cleaned:
        return _ParseDetails(None, PARSE_REASON_EMPTY)

    # Rule 2: the whole answer is a single letter.
    if _SINGLE_LETTER_RE.fullmatch(cleaned):
        letter = cleaned.upper()
        if letter in allowed:
            return _ParseDetails(letter, PARSE_REASON_OK, (letter,))
        return _ParseDetails(letter, PARSE_REASON_OUT_OF_RANGE, (letter,))

    # Rule 3: an explicit pattern is authoritative.  The rest of the response
    # is not scanned for other letters; only a direct list of answers makes
    # the response ambiguous.
    explicit = _collect_explicit(cleaned)
    if explicit:
        letter = explicit[0]
        listed = _listed_letters(cleaned, letter, allowed)
        if listed:
            return _ParseDetails(None, PARSE_REASON_MULTIPLE, tuple(sorted(listed)))
        if letter in allowed:
            return _ParseDetails(letter, PARSE_REASON_OK, (letter,))
        if not _scan_uppercase_letters(cleaned, allowed):
            # The response names a letter this quiz does not offer.
            return _ParseDetails(letter, PARSE_REASON_OUT_OF_RANGE, (letter,))
        # The captured letter is not a choice of this quiz; fall through so a
        # real choice can still be found.

    # Rule 4: fallback.
    return _fallback_details(cleaned, allowed)


def parse_choice_letter(candidate: str, num_choices: int) -> ParseResult:
    """Parse a free-form answer into a single choice letter.

    Args:
        candidate: Raw model answer.
        num_choices: Number of choices offered (A..A+num_choices-1).

    Returns:
        A :class:`~nichebench.metrics.types.ParseResult` whose ``reason`` is
        one of ``ok``, ``empty``, ``no_letter``, ``multiple_letters`` or
        ``out_of_range``.
    """
    details = _parse_details(candidate, num_choices)
    return ParseResult(letter=details.letter, reason=details.reason)


def _normalize_gold(gold: str, num_choices: int) -> str:
    """Validate and normalize the gold letter; raise ``ValueError`` if invalid."""
    normalized = (gold or "").strip().upper()
    if len(normalized) != 1 or not normalized.isalpha():
        raise ValueError(f"gold must be a single letter, got {gold!r}")
    allowed = _allowed_letters(num_choices)
    if normalized not in allowed:
        raise ValueError(
            f"gold {normalized!r} is out of range for {num_choices} choices "
            f"(allowed: {', '.join(sorted(allowed))})"
        )
    return normalized


def _explain(details: _ParseDetails, gold: str, num_choices: int) -> str:
    if details.reason == PARSE_REASON_OK:
        return f"Selected {details.letter}; gold {gold}"
    if details.reason == PARSE_REASON_MULTIPLE:
        letters = ", ".join(details.letters)
        return f"Ambiguous letters: {letters} ({details.reason})"
    if details.reason == PARSE_REASON_OUT_OF_RANGE:
        allowed = ", ".join(sorted(_allowed_letters(num_choices)))
        return f"No parseable letter ({details.reason}): detected {details.letter}, allowed {allowed}"
    return f"No parseable letter ({details.reason})"


def score_quiz(candidate: str, gold: str, num_choices: int) -> QuizVerdict:
    """Score a multiple-choice answer deterministically.

    The answer is parsed into a single letter and compared with ``gold``.
    No judge model, no network, no retries.

    Raises:
        ValueError: When ``num_choices`` or ``gold`` is invalid.  That is a
            programming/data error, not a model failure.
    """
    _validate_num_choices(num_choices)
    gold_letter = _normalize_gold(gold, num_choices)
    details = _parse_details(candidate, num_choices)

    selected = details.letter
    passed = selected is not None and selected == gold_letter
    score = 1.0 if passed else 0.0
    explanation = _explain(details, gold_letter, num_choices)
    return QuizVerdict(
        selected=selected,
        gold=gold_letter,
        passed=passed,
        score=score,
        explanation=explanation,
        method="deterministic",
        parse_reason=details.reason,
    )
