"""Golden prompt tests for the quiz-v2 prompt composition.

Covers:

* the MUT prompt renders every choice exactly once with a dynamic letter;
* redundant letter prefixes (``A) text``, ``A) A) text``, ``(A) text``) never
  survive into the prompt;
* the letter space adapts to the number of choices (``A``–``F`` for six,
  ``A``–``D`` for four);
* the fallbacks for a missing context and a missing choice list;
* a regression test against the real dataset files ``quiz_004``/``quiz_057``
  (skipped when the private data submodule is absent);
* the removed ``start_quiz_conversation`` path stays removed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.framework_loader import load_testcase_from_file
from nichebench.providers.mut_prompt_composer import MUTPromptComposer

REPO_ROOT = Path(__file__).resolve().parents[3]
QUIZ_DATA_DIR = REPO_ROOT / "src" / "nichebench" / "frameworks" / "drupal" / "data" / "quiz"

PLAIN_CHOICES = [
    "First option",
    "Second option",
    "Third option",
    "Fourth option",
    "Fifth option",
    "Sixth option",
]

EXPECTED_SIX_CHOICE_PROMPT = (
    "Question: Which option is correct?\n"
    "\n"
    "Choices:\n"
    "A) First option\n"
    "B) Second option\n"
    "C) Third option\n"
    "D) Fourth option\n"
    "E) Fifth option\n"
    "F) Sixth option\n"
    "\n"
    "Your answer (letter only):"
)


def _quiz_spec(
    choices: Optional[List[str]] = None,
    question: str = "Which option is correct?",
    context: Optional[str] = None,
) -> TestCaseSpec:
    return TestCaseSpec(
        id="quiz_prompt_001",
        type="quiz",
        raw={"question": question, "choices": choices or []},
        context=context,
        choices=choices,
    )


def _choices_block(prompt: str) -> str:
    """Return the ``Choices:`` block of a rendered prompt."""
    lines = prompt.splitlines()
    start = lines.index("Choices:")
    end = start + 1
    while end < len(lines) and re.match(r"^[A-Z]\) ", lines[end]):
        end += 1
    return "\n".join(lines[start:end])


def test_six_plain_choices_render_exactly_once() -> None:
    """Six plain choices produce the exact expected prompt with ``F)`` present."""
    prompt = MUTPromptComposer.compose_quiz_prompt(_quiz_spec(choices=PLAIN_CHOICES))

    assert prompt == EXPECTED_SIX_CHOICE_PROMPT
    assert "F) Sixth option" in prompt
    assert "A) A)" not in prompt
    assert prompt.count("Choices:") == 1


def test_prefixed_choices_render_identically() -> None:
    """Redundant letter prefixes are stripped; the rendered prompt is identical."""
    prefixed = [
        "A) First option",
        "A) A) Second option",
        "(A) Third option",
        "D) Fourth option",
        "E) Fifth option",
        "F) Sixth option",
    ]

    prompt = MUTPromptComposer.compose_quiz_prompt(_quiz_spec(choices=prefixed))

    assert prompt == EXPECTED_SIX_CHOICE_PROMPT
    assert "A) A)" not in prompt
    assert "F) Sixth option" in prompt


def test_four_choices_use_only_a_to_d() -> None:
    """A four-choice question renders no ``E)`` and no ``F)``."""
    choices = ["First option", "Second option", "Third option", "Fourth option"]

    prompt = MUTPromptComposer.compose_quiz_prompt(_quiz_spec(choices=choices))

    assert "D) Fourth option" in prompt
    assert "E)" not in prompt
    assert "F)" not in prompt


def test_missing_choices_falls_back_to_plain_answer_request() -> None:
    """Without choices the prompt asks for a plain answer."""
    prompt = MUTPromptComposer.compose_quiz_prompt(_quiz_spec(choices=None))

    assert "Choices:" not in prompt
    assert "Your answer:" in prompt
    assert "Your answer (letter only):" not in prompt


def test_missing_context_emits_no_context_line() -> None:
    """A missing context does not produce an empty ``Context:`` line."""
    prompt = MUTPromptComposer.compose_quiz_prompt(_quiz_spec(choices=PLAIN_CHOICES))

    assert "Context:" not in prompt
    assert prompt.startswith("Question:")


def test_present_context_is_rendered_once() -> None:
    """A present context is rendered exactly once."""
    prompt = MUTPromptComposer.compose_quiz_prompt(
        _quiz_spec(choices=PLAIN_CHOICES, context="Drupal is a content management framework.")
    )

    assert prompt.count("Context: Drupal is a content management framework.") == 1


def test_system_prompt_is_rendered_once() -> None:
    """The system prompt is prepended once."""
    prompt = MUTPromptComposer.compose_quiz_prompt(
        _quiz_spec(choices=PLAIN_CHOICES), "You are a Drupal expert."
    )

    assert prompt.startswith("You are a Drupal expert.")
    assert prompt.count("You are a Drupal expert.") == 1


def test_start_quiz_conversation_is_removed() -> None:
    """Quiz is single-turn; the unused conversation path is gone."""
    assert not hasattr(MUTPromptComposer, "start_quiz_conversation")


@pytest.mark.parametrize("quiz_id", ["quiz_004", "quiz_057"])
def test_real_dataset_prompt_has_no_double_letters(quiz_id: str) -> None:
    """Regression test against the real dataset: six single-lettered choices."""
    path = QUIZ_DATA_DIR / f"{quiz_id}.yaml"
    if not path.exists():
        pytest.skip(f"quiz dataset not available: {path}")

    spec = load_testcase_from_file(path)
    prompt = MUTPromptComposer.compose_quiz_prompt(spec)

    assert "A) A)" not in prompt
    assert "B) B)" not in prompt
    assert "C) C)" not in prompt

    for letter in "ABCDEF":
        occurrences = re.findall(rf"(?m)^{letter}\) ", prompt)
        assert len(occurrences) == 1, f"{letter}) should appear exactly once"

    assert "F) " in prompt
    assert "Your answer (letter only):" in prompt


@pytest.mark.parametrize("quiz_id", ["quiz_004", "quiz_057"])
def test_real_dataset_choices_are_normalised_once(quiz_id: str) -> None:
    """The loader strips the prefix, the composer does not add it twice."""
    path = QUIZ_DATA_DIR / f"{quiz_id}.yaml"
    if not path.exists():
        pytest.skip(f"quiz dataset not available: {path}")

    spec = load_testcase_from_file(path)

    assert spec.choices is not None
    assert len(spec.choices) == 6
    for choice in spec.choices:
        assert not re.match(r"^\s*\(?[A-Fa-f]\s*[\)\].:\-]\s*", choice)
