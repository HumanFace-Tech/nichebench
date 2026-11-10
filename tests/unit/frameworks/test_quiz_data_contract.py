"""Data contract tests for the quiz dataset.

Two sources are checked:

* the real quiz dataset under
  ``src/nichebench/frameworks/drupal/data/quiz`` — skipped when the private
  data submodule is not checked out;
* the synthetic fixture ``tests/fixtures/quiz/quiz_v2_fixture.yaml`` — always
  present, 12 items with 6 plain-text choices each;
* the sanitized CI dataset ``tests/fixtures/quiz/ci_dataset`` — always present,
  12 committed YAML files that the CI data gate audits when the private
  submodule is absent.

The contract encodes the quiz-v2 rules: a well-formed item, 2-6 choices, a
``correct_choice`` inside the available range, no repeated choice text inside
one question, and no duplicated question identity across the dataset.

"Question identity" hashes the question together with its context and summary.
The bare question stem is not enough: several items in the private dataset
share a generic stem such as "What should you do?" while describing different
topics. The hash deliberately excludes the options, so reordering options or
stripping letter prefixes does not change an item's identity.

Repeated choice text is checked on the *normalized* text: the redundant
letter prefixes hide duplicates (``A) Set`` and ``B) Set`` are the same
option for the model).  The real dataset was repaired and rebalanced in
place, so the prefix, distribution, duplicate, and truncation checks all run
as normal assertions.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from nichebench.core.quiz_text import (
    find_truncated_options,
    has_choice_prefix,
    normalize_choice,
    read_choice_sources,
    render_choice,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_DATA_DIR = REPO_ROOT / "src" / "nichebench" / "frameworks" / "drupal" / "data" / "quiz"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "quiz" / "quiz_v2_fixture.yaml"
CI_DATASET_DIR = REPO_ROOT / "tests" / "fixtures" / "quiz" / "ci_dataset"

REQUIRED_KEYS = ("id", "question", "choices", "correct_choice")
LETTERS = "ABCDEF"
MIN_CHOICES = 2
MAX_CHOICES = 6
MIN_GOLD_SHARE = 0.08


def normalize(text: Any) -> str:
    """Lower-case the text and collapse whitespace runs to single spaces."""
    return " ".join(str(text or "").lower().split())


def question_hash(item: Dict[str, Any]) -> str:
    """Identity hash of a quiz item.

    The hash covers the question together with its context and summary, so it
    identifies the *item* rather than the bare question stem. Bare stems do
    repeat in the private dataset (several items share stems such as
    "What should you do?"), while no two items share the same context. The
    hash deliberately excludes the options so that reordering options or
    stripping letter prefixes does not change the identity of an item.
    """
    payload = "|".join(
        (
            normalize(item.get("question")),
            normalize(item.get("context")),
            normalize(item.get("summary")),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def choice_text(choice: Any) -> str:
    """Render a raw choice value as text.

    Five files in the private dataset contain an unquoted ``key: value`` inside
    the option text, which YAML parses as a mapping rather than a string. Those
    values are rendered back to ``key: value`` so that the rest of the contract
    sees the original wording.  The rendering lives in
    ``nichebench.core.quiz_text``, so the contract, the audit and the loader
    cannot disagree about it.
    """
    return render_choice(choice)


def has_letter_prefix(choice: Any) -> bool:
    """True when the option text still carries a redundant ``A) `` style prefix.

    Delegates to the canonical predicate in ``nichebench.core.quiz_text``, so
    the contract test, the audit and the loader cannot drift apart.
    """
    return has_choice_prefix(choice)


def load_real_items() -> List[Dict[str, Any]]:
    """Load every quiz item from the real dataset."""
    items = []
    for path in sorted(REAL_DATA_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} does not parse to a mapping"
        items.append(data)
    return items


def load_fixture_items() -> List[Dict[str, Any]]:
    """Load the 12 synthetic items from the quiz-v2 fixture."""
    data = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list), "fixture must contain a list of quiz items"
    return data


def load_ci_dataset_items() -> List[Dict[str, Any]]:
    """Load the items of the sanitized CI dataset (one YAML file per item)."""
    items = []
    for path in sorted(CI_DATASET_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} does not parse to a mapping"
        items.append(data)
    return items


@pytest.fixture(scope="module")
def real_items() -> List[Dict[str, Any]]:
    """Real dataset items, or a skip when the private submodule is absent."""
    if not REAL_DATA_DIR.is_dir():
        pytest.skip(f"quiz dataset not available at {REAL_DATA_DIR}")
    return load_real_items()


@pytest.fixture(scope="module")
def fixture_items() -> List[Dict[str, Any]]:
    """Synthetic quiz-v2 fixture items."""
    return load_fixture_items()


@pytest.fixture(scope="module")
def ci_dataset_items() -> List[Dict[str, Any]]:
    """The 12 items of the sanitized CI dataset.

    This dataset is committed to the repository, so the check never skips:
    CI checks out without the private data submodule and the data gate still
    has to run against something.
    """
    assert CI_DATASET_DIR.is_dir(), f"CI dataset fixture missing at {CI_DATASET_DIR}"
    return load_ci_dataset_items()


def assert_schema(item: Dict[str, Any]) -> None:
    """Assert the structural contract for a single quiz item."""
    for key in REQUIRED_KEYS:
        assert key in item, f"missing key {key!r}"

    choices = item["choices"]
    assert isinstance(choices, list), "choices must be a list"
    assert MIN_CHOICES <= len(choices) <= MAX_CHOICES, f"expected {MIN_CHOICES}-{MAX_CHOICES} choices"
    assert all(choice_text(choice).strip() for choice in choices), "choices must not be empty"

    gold = item["correct_choice"]
    assert isinstance(gold, str) and gold.upper() in LETTERS[: len(choices)], f"correct_choice {gold!r} out of range"

    assert normalize(item["question"]), "question must not be empty"


def assert_unique_choices(item: Dict[str, Any]) -> None:
    """Assert no option text repeats inside one question.

    The check runs on the normalized text: the redundant letter prefixes hide
    duplicates (``A) Set`` and ``B) Set`` are the same option for the model).
    """
    choices = [normalize_choice(choice) for choice in item["choices"]]
    duplicates = {choice for choice in choices if choices.count(choice) > 1}
    assert not duplicates, f"duplicate choices: {sorted(duplicates)}"


def assert_unique_question_hashes(items: List[Dict[str, Any]]) -> None:
    """Assert no two items share the same question identity."""
    seen: Dict[str, Dict[str, Any]] = {}
    for item in items:
        digest = question_hash(item)
        assert digest not in seen, f"duplicate question: {item.get('id')} and {seen[digest].get('id')}"
        seen[digest] = item


def assert_gold_distribution(items: List[Dict[str, Any]]) -> None:
    """Assert every letter carries a non-trivial share of the gold answers."""
    total = len(items)
    if total < 60:
        pytest.skip("gold distribution check applies to datasets with 60+ items")
    counts = {letter: 0 for letter in LETTERS}
    for item in items:
        counts[str(item["correct_choice"]).upper()] += 1
    for letter, count in counts.items():
        assert count / total >= MIN_GOLD_SHARE, f"gold {letter} is only {count}/{total} of the dataset"


# ---------------------------------------------------------------------------
# Real dataset
# ---------------------------------------------------------------------------


def test_real_dataset_has_items(real_items: List[Dict[str, Any]]) -> None:
    """The real dataset is not empty."""
    assert real_items, "no quiz items found"


def test_real_items_match_schema(real_items: List[Dict[str, Any]]) -> None:
    """Every real item satisfies the structural contract."""
    for item in real_items:
        assert_schema(item)


def test_real_items_have_unique_choices(real_items: List[Dict[str, Any]]) -> None:
    """No real item repeats an option text after prefix normalization."""
    for item in real_items:
        assert_unique_choices(item)


def test_real_items_have_unique_question_hashes(real_items: List[Dict[str, Any]]) -> None:
    """No two real items describe the same question."""
    assert_unique_question_hashes(real_items)


def test_real_items_have_no_truncated_choices(real_items: List[Dict[str, Any]]) -> None:
    """No real option is cut short by a YAML comment."""
    assert real_items, "the real dataset is empty"
    for path in sorted(REAL_DATA_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        sources = read_choice_sources(text) or []
        truncated = find_truncated_options(data.get("choices") or [], sources)
        assert not truncated, f"{path.name} has truncated options: {[item['index'] for item in truncated]}"


def test_real_choices_have_no_letter_prefix(real_items: List[Dict[str, Any]]) -> None:
    """Option text carries no redundant letter prefix."""
    for item in real_items:
        for choice in item["choices"]:
            assert not has_letter_prefix(choice), f"letter prefix in {item.get('id')}: {choice!r}"


def test_real_gold_distribution_is_balanced(real_items: List[Dict[str, Any]]) -> None:
    """Gold answers are spread over all six letters."""
    assert_gold_distribution(real_items)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def test_fixture_has_twelve_items(fixture_items: List[Dict[str, Any]]) -> None:
    """The fixture holds exactly 12 synthetic items."""
    assert len(fixture_items) == 12


def test_fixture_items_match_schema(fixture_items: List[Dict[str, Any]]) -> None:
    """Every fixture item satisfies the structural contract."""
    for item in fixture_items:
        assert_schema(item)


def test_fixture_items_have_unique_choices(fixture_items: List[Dict[str, Any]]) -> None:
    """No fixture item repeats an option text."""
    for item in fixture_items:
        assert_unique_choices(item)


def test_fixture_items_have_unique_question_hashes(fixture_items: List[Dict[str, Any]]) -> None:
    """No two fixture items describe the same question."""
    assert_unique_question_hashes(fixture_items)


def test_fixture_choices_have_no_letter_prefix(fixture_items: List[Dict[str, Any]]) -> None:
    """Fixture option text is plain, without redundant letter prefixes."""
    for item in fixture_items:
        for choice in item["choices"]:
            assert not has_letter_prefix(choice), f"letter prefix in {item.get('id')}: {choice!r}"


def test_fixture_gold_distribution_is_balanced(fixture_items: List[Dict[str, Any]]) -> None:
    """Fixture gold answers cover every letter evenly."""
    counts = {letter: 0 for letter in LETTERS}
    for item in fixture_items:
        counts[str(item["correct_choice"]).upper()] += 1
    assert counts == {"A": 2, "B": 2, "C": 2, "D": 2, "E": 2, "F": 2}


# ---------------------------------------------------------------------------
# CI dataset (sanitized, committed - never skipped)
# ---------------------------------------------------------------------------


def test_ci_dataset_has_twelve_items(ci_dataset_items: List[Dict[str, Any]]) -> None:
    """The CI dataset holds exactly 12 items, one per file."""
    assert len(ci_dataset_items) == 12


def test_ci_dataset_items_match_schema(ci_dataset_items: List[Dict[str, Any]]) -> None:
    """Every CI dataset item satisfies the structural contract."""
    for item in ci_dataset_items:
        assert_schema(item)
        assert item.get("dataset_version") == "quiz-v2", (
            f"{item.get('id')} must carry dataset_version 'quiz-v2'"
        )


def test_ci_dataset_items_have_six_choices(ci_dataset_items: List[Dict[str, Any]]) -> None:
    """Every CI dataset item has exactly six options."""
    for item in ci_dataset_items:
        assert len(item["choices"]) == 6, f"{item.get('id')} has {len(item['choices'])} choices"


def test_ci_dataset_items_have_unique_choices(ci_dataset_items: List[Dict[str, Any]]) -> None:
    """No CI dataset item repeats an option text."""
    for item in ci_dataset_items:
        assert_unique_choices(item)


def test_ci_dataset_choices_have_no_letter_prefix(ci_dataset_items: List[Dict[str, Any]]) -> None:
    """CI dataset option text is plain, without redundant letter prefixes."""
    for item in ci_dataset_items:
        for choice in item["choices"]:
            assert not has_letter_prefix(choice), f"letter prefix in {item.get('id')}: {choice!r}"


def test_ci_dataset_gold_distribution_is_balanced(ci_dataset_items: List[Dict[str, Any]]) -> None:
    """CI dataset gold answers cover every letter exactly twice."""
    counts = {letter: 0 for letter in LETTERS}
    for item in ci_dataset_items:
        counts[str(item["correct_choice"]).upper()] += 1
    assert counts == {"A": 2, "B": 2, "C": 2, "D": 2, "E": 2, "F": 2}


# ---------------------------------------------------------------------------
# Detection of the defects the private dataset still carries
# ---------------------------------------------------------------------------


def test_duplicate_choices_are_detected_after_the_prefix_is_stripped() -> None:
    """``A) Set`` and ``B) Set`` are duplicates once the prefixes are gone."""
    item = {
        "id": "drupal_quiz_999",
        "question": "Question?",
        "choices": ["A) Set", "B) Set", "C) Another option"],
        "correct_choice": "A",
    }

    with pytest.raises(AssertionError, match="duplicate choices"):
        assert_unique_choices(item)


def test_distinct_choices_pass_the_duplicate_check() -> None:
    """Options that only look alike are not duplicates."""
    item = {
        "id": "drupal_quiz_999",
        "question": "Question?",
        "choices": ["A) Set", "B) Set the value", "C) Another option"],
        "correct_choice": "A",
    }

    assert_unique_choices(item)


def test_a_truncated_option_is_detected(tmp_path: Path) -> None:
    """An unquoted ``#`` cuts the option text short, and the helper sees it."""
    path = tmp_path / "drupal_quiz_999.yaml"
    path.write_text(
        "id: drupal_quiz_999\n"
        "question: Question?\n"
        "choices:\n"
        "  - A) Set #cache['max-age'] = 0 so caching is respected.\n"
        "  - B) Another option\n"
        "correct_choice: A\n",
        encoding="utf-8",
    )
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    sources = read_choice_sources(text) or []

    truncated = find_truncated_options(data["choices"], sources)

    assert len(truncated) == 1
    entry = truncated[0]
    assert entry["index"] == 0
    assert entry["parsed"] == "Set"
    assert entry["authored_prefix"] == "Set"
    assert entry["comment"] == "#cache['max-age'] = 0 so caching is respected."
