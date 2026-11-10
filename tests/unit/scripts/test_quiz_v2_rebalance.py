"""Unit tests for scripts/quiz_v2_rebalance.py.

These tests never touch the real quiz dataset: every scenario builds a small
synthetic dataset in ``tmp_path``.

Coverage:
- the gold letter ends up balanced across A-F,
- the option multiset is preserved (no option text is lost),
- the output is deterministic for a given seed and idempotent across runs,
- redundant letter prefixes (``A) ``, ``A. ``, ``A: ``, ``A - ``, ``(A) ``) are
  stripped,
- a choice that YAML parses as a mapping is rendered as ``key: value`` and
  re-emitted as a quoted string,
- invalid input (duplicate choices, out-of-range ``correct_choice``, too few
  choices) is rejected without touching the data,
- ``--verify`` passes after ``--write`` and fails on a mutated copy.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

REBALANCE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "quiz_v2_rebalance.py"
SEED = "quiz-v2-2026-11"


def _load_module():
    """Load the script by path; it lives outside the package on purpose."""
    spec = importlib.util.spec_from_file_location("quiz_v2_rebalance", REBALANCE_PATH)
    assert spec and spec.loader, "quiz_v2_rebalance.py must exist and be importable"
    module = importlib.util.module_from_spec(spec)
    sys.modules["quiz_v2_rebalance"] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("quiz_v2_rebalance", None)


@pytest.fixture(scope="module")
def rebalance():
    return _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_questions(count: int = 6) -> List[Dict[str, Any]]:
    """Build ``count`` questions with a known gold letter each."""
    questions = []
    for index in range(count):
        gold = "ABCDEF"[index % 6]
        choices = [f"{letter}) Option {letter} of question {index + 1}" for letter in "ABCDEF"]
        questions.append(
            {
                "id": f"drupal_quiz_{index + 1:03d}",
                "context": f"Context of question {index + 1}",
                "summary": f"Summary of question {index + 1}",
                "question": f"Question {index + 1}?",
                "choices": choices,
                "correct_choice": gold,
            }
        )
    return questions


def write_dataset(root: Path, questions: List[Dict[str, Any]]) -> Path:
    """Write a dataset of quiz files and return the directory."""
    root.mkdir(parents=True, exist_ok=True)
    for question in questions:
        path = root / f"{question['id']}.yaml"
        path.write_text(
            yaml.safe_dump(question, sort_keys=False, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    return root


def write_truncated_dataset(root: Path) -> Path:
    """Write a dataset whose first option is truncated by a YAML comment.

    The option is written unquoted, so YAML cuts it at the ``#``.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "drupal_quiz_001.yaml").write_text(
        "id: drupal_quiz_001\n"
        "question: Question 1?\n"
        "choices:\n"
        "  - A) Set #cache['max-age'] = 0 so caching is respected.\n"
        "  - B) Second option\n"
        "  - C) Third option\n"
        "  - D) Fourth option\n"
        "  - E) Fifth option\n"
        "  - F) Sixth option\n"
        "correct_choice: A\n",
        encoding="utf-8",
    )
    return root


def read_choices(path: Path) -> List[str]:
    """Return the choices of a quiz file as they are stored."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data["choices"])


def file_bytes(root: Path) -> Dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(root.glob("*.yaml"))}


def migrate(
    rebalance,
    data_dir: Path,
    manifest_path: Path = None,
    seed: str = SEED,
    allow_truncated: bool = False,
) -> int:
    """Run the migration on ``data_dir`` and return its exit code."""
    argv = ["--data-dir", str(data_dir), "--write", "--seed", seed]
    if manifest_path is not None:
        argv += ["--manifest", str(manifest_path)]
    if allow_truncated:
        argv.append("--allow-truncated")
    return rebalance.main(argv)


# ---------------------------------------------------------------------------
# Balancing and content preservation
# ---------------------------------------------------------------------------


def test_balanced_distribution(rebalance, tmp_path):
    """Six questions with one gold letter each end up one letter per position."""
    data_dir = write_dataset(tmp_path / "data", build_questions(6))
    manifest_path = tmp_path / "manifest.json"

    exit_code = rebalance.main(
        ["--data-dir", str(data_dir), "--write", "--seed", SEED, "--manifest", str(manifest_path)]
    )
    assert exit_code == 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["after_distribution"] == {"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1}
    assert manifest["before_distribution"] == {"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1}
    assert manifest["dataset_version"] == "quiz-v2"
    assert manifest["seed"] == SEED
    assert manifest["question_count"] == 6
    assert "generated_at" in manifest

    golds = sorted(
        yaml.safe_load(path.read_text(encoding="utf-8"))["correct_choice"] for path in sorted(data_dir.glob("*.yaml"))
    )
    assert golds == ["A", "B", "C", "D", "E", "F"]


def test_content_preservation(rebalance, tmp_path):
    """Every option text survives the migration (multiset equality)."""
    questions = build_questions(6)
    data_dir = write_dataset(tmp_path / "data", questions)
    before = {
        question["id"]: sorted(rebalance.normalize_choice(choice) for choice in question["choices"])
        for question in questions
    }

    assert migrate(rebalance, data_dir) == 0

    for path in sorted(data_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        after = sorted(rebalance.normalize_choice(c) for c in data["choices"])
        assert after == before[data["id"]]


def test_manifest_records_content_hashes(rebalance, tmp_path):
    """The manifest carries the before/after multiset hashes per question."""
    data_dir = write_dataset(tmp_path / "data", build_questions(6))
    manifest_path = tmp_path / "manifest.json"
    assert rebalance.main(["--data-dir", str(data_dir), "--write", "--manifest", str(manifest_path)]) == 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {entry["id"]: entry for entry in manifest["questions"]}
    assert set(entries) == {f"drupal_quiz_{index:03d}" for index in range(1, 7)}
    for entry in entries.values():
        assert entry["old_multiset_sha256"] == entry["new_multiset_sha256"]
        assert entry["old_correct"] in "ABCDEF"
        assert entry["new_correct"] in "ABCDEF"


def test_deterministic_for_the_same_seed(rebalance, tmp_path):
    """The same seed produces byte-identical output on two identical datasets."""
    first = write_dataset(tmp_path / "first", build_questions(6))
    second = write_dataset(tmp_path / "second", build_questions(6))

    assert rebalance.main(["--data-dir", str(first), "--write", "--seed", SEED]) == 0
    assert rebalance.main(["--data-dir", str(second), "--write", "--seed", SEED]) == 0
    assert file_bytes(first) == file_bytes(second)


def test_idempotent(rebalance, tmp_path):
    """Running the migration twice with the same seed changes nothing."""
    data_dir = write_dataset(tmp_path / "data", build_questions(6))
    manifest_path = tmp_path / "manifest.json"

    assert migrate(rebalance, data_dir, manifest_path) == 0
    first = file_bytes(data_dir)

    assert migrate(rebalance, data_dir, manifest_path) == 0
    assert file_bytes(data_dir) == first

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["questions"]:
        path = data_dir / f"{entry['id']}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["correct_choice"] == entry["new_correct"]


# ---------------------------------------------------------------------------
# Prefix stripping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["A) text", "A. text", "A: text", "A - text", "(A) text", "text", "A) A) text"],
)
def test_prefix_stripping(rebalance, raw):
    """Redundant letter markers are removed, plain text is left alone."""
    assert rebalance.strip_choice_prefix(raw) == "text"


def test_no_prefix_survives_in_the_files(rebalance, tmp_path):
    """No option of the migrated dataset starts with a letter marker."""
    questions = build_questions(6)
    for question in questions:
        question["choices"] = [
            f"{letter}) Option {letter} of {question['id']}" for letter in "ABCDEF"
        ]
    data_dir = write_dataset(tmp_path / "data", questions)

    assert migrate(rebalance, data_dir) == 0
    for path in sorted(data_dir.glob("*.yaml")):
        for choice in read_choices(path):
            assert rebalance.strip_choice_prefix(choice) == choice


# ---------------------------------------------------------------------------
# Non-string choices
# ---------------------------------------------------------------------------


def test_non_string_choice_is_rendered_and_quoted(rebalance, tmp_path):
    """A choice that YAML parses as a mapping becomes a quoted ``key: value``."""
    questions = build_questions(6)
    questions[0]["choices"][2] = {"module_name.hooks_converted": True}
    data_dir = write_dataset(tmp_path / "data", questions)

    assert migrate(rebalance, data_dir) == 0

    path = data_dir / "drupal_quiz_001.yaml"
    text = path.read_text(encoding="utf-8")
    assert "module_name.hooks_converted: true" in text
    # The value is re-emitted as a quoted string, not as a mapping.
    assert "'module_name.hooks_converted: true'" in text
    choices = yaml.safe_load(text)["choices"]
    assert all(isinstance(choice, str) for choice in choices)
    assert "module_name.hooks_converted: true" in choices


def test_non_string_choice_round_trip_keeps_the_text(rebalance, tmp_path):
    """The rendered mapping text is the text the harness sees."""
    rendered = rebalance.choice_text({"module_name.hooks_converted": True})
    assert rendered == "module_name.hooks_converted: true"
    assert rebalance.normalize_choice({"module_name.hooks_converted": True}) == (
        "module_name.hooks_converted: true"
    )


# ---------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------


def _invalid_dataset(root: Path, questions: List[Dict[str, Any]]) -> Path:
    return write_dataset(root, questions)


def test_duplicate_choices_are_rejected(rebalance, tmp_path):
    """Two identical options are a data contract violation."""
    questions = build_questions(1)
    questions[0]["choices"][1] = "A) Option A of question 1"
    data_dir = _invalid_dataset(tmp_path / "data", questions)
    before = file_bytes(data_dir)

    exit_code = migrate(rebalance, data_dir)
    assert exit_code == 1
    assert file_bytes(data_dir) == before


def test_out_of_range_correct_choice_is_rejected(rebalance, tmp_path):
    """``correct_choice`` must point at an existing option."""
    questions = build_questions(1)
    questions[0]["choices"] = questions[0]["choices"][:3]
    questions[0]["correct_choice"] = "F"
    data_dir = _invalid_dataset(tmp_path / "data", questions)
    before = file_bytes(data_dir)

    exit_code = migrate(rebalance, data_dir)
    assert exit_code == 1
    assert file_bytes(data_dir) == before


def test_too_few_choices_are_rejected(rebalance, tmp_path):
    """A quiz needs at least two options."""
    questions = build_questions(1)
    questions[0]["choices"] = questions[0]["choices"][:1]
    data_dir = _invalid_dataset(tmp_path / "data", questions)
    before = file_bytes(data_dir)

    exit_code = migrate(rebalance, data_dir)
    assert exit_code == 1
    assert file_bytes(data_dir) == before


def test_invalid_file_is_reported_and_the_rest_is_migrated(rebalance, tmp_path, capsys):
    """One broken file does not stop the migration of the other files."""
    questions = build_questions(6)
    questions[2]["choices"] = questions[2]["choices"][:1]
    data_dir = _invalid_dataset(tmp_path / "data", questions)

    exit_code = migrate(rebalance, data_dir)
    assert exit_code == 1
    output = capsys.readouterr().out
    assert "drupal_quiz_003.yaml" in output
    assert "rejected files" in output


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_verify_passes_after_write(rebalance, tmp_path):
    """``--verify`` accepts a dataset that was just migrated."""
    data_dir = write_dataset(tmp_path / "data", build_questions(6))
    manifest_path = tmp_path / "manifest.json"
    assert migrate(rebalance, data_dir, manifest_path) == 0

    assert rebalance.main(["--data-dir", str(data_dir), "--verify", str(manifest_path)]) == 0


def test_verify_fails_on_a_mutated_copy(rebalance, tmp_path):
    """``--verify`` rejects a dataset whose option text was changed."""
    data_dir = write_dataset(tmp_path / "data", build_questions(6))
    manifest_path = tmp_path / "manifest.json"
    assert migrate(rebalance, data_dir, manifest_path) == 0

    mutated = shutil.copytree(data_dir, tmp_path / "mutated")
    path = mutated / "drupal_quiz_001.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["choices"][0] = "Tampered option"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    assert rebalance.main(["--data-dir", str(mutated), "--verify", str(manifest_path)]) == 1


def test_verify_fails_on_a_changed_gold_letter(rebalance, tmp_path):
    """``--verify`` rejects a dataset whose gold letter was changed."""
    data_dir = write_dataset(tmp_path / "data", build_questions(6))
    manifest_path = tmp_path / "manifest.json"
    assert migrate(rebalance, data_dir, manifest_path) == 0

    mutated = shutil.copytree(data_dir, tmp_path / "mutated")
    path = mutated / "drupal_quiz_002.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["correct_choice"] = "A" if data["correct_choice"] != "A" else "B"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    assert rebalance.main(["--data-dir", str(mutated), "--verify", str(manifest_path)]) == 1


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(rebalance, tmp_path, capsys):
    """``--dry-run`` reports the change without touching the data."""
    data_dir = write_dataset(tmp_path / "data", build_questions(6))
    before = file_bytes(data_dir)

    exit_code = rebalance.main(["--data-dir", str(data_dir), "--dry-run", "--seed", SEED])
    assert exit_code == 0
    assert file_bytes(data_dir) == before

    output = capsys.readouterr().out
    assert "before distribution" in output
    assert "after distribution" in output
    assert "files that change" in output
    assert "unified diff" in output


def test_truncated_option_is_reported(rebalance, tmp_path, capsys):
    """An option truncated by a YAML comment is reported as a warning."""
    data_dir = write_truncated_dataset(tmp_path / "data")

    assert rebalance.main(["--data-dir", str(data_dir), "--dry-run", "--seed", SEED]) == 0
    output = capsys.readouterr().out
    assert "truncated by a YAML comment" in output


def test_truncated_option_keeps_its_source_text(rebalance, tmp_path):
    """The text behind a YAML comment survives the rewrite."""
    data_dir = write_truncated_dataset(tmp_path / "data")

    assert migrate(rebalance, data_dir, allow_truncated=True) == 0

    text = (data_dir / "drupal_quiz_001.yaml").read_text(encoding="utf-8")
    assert "Set #cache['max-age'] = 0 so caching is respected." in text
    # The parsed option text is unchanged: the migration does not reword it.
    choices = yaml.safe_load(text)["choices"]
    assert all(isinstance(choice, str) for choice in choices)
    assert "Set" in choices


# ---------------------------------------------------------------------------
# The truncation gate
# ---------------------------------------------------------------------------


def test_write_refuses_a_truncated_option(rebalance, tmp_path, capsys):
    """``--write`` refuses to touch a dataset with a truncated option."""
    data_dir = write_truncated_dataset(tmp_path / "data")
    before = file_bytes(data_dir)

    exit_code = migrate(rebalance, data_dir)

    assert exit_code != 0
    assert file_bytes(data_dir) == before

    error = capsys.readouterr().err
    assert "refusing to write" in error
    assert "drupal_quiz_001.yaml" in error
    # The message names the affected option and quotes the authored line.
    assert "parsed='Set'" in error
    assert "Set #cache['max-age'] = 0 so caching is respected." in error


def test_allow_truncated_permits_the_write(rebalance, tmp_path):
    """``--allow-truncated`` is the explicit escape hatch (tests only)."""
    data_dir = write_truncated_dataset(tmp_path / "data")
    before = file_bytes(data_dir)

    assert migrate(rebalance, data_dir, allow_truncated=True) == 0
    assert file_bytes(data_dir) != before


def test_dry_run_still_reports_a_truncated_option(rebalance, tmp_path):
    """The dry run is a report, not a gate: it succeeds and warns."""
    data_dir = write_truncated_dataset(tmp_path / "data")
    before = file_bytes(data_dir)

    assert rebalance.main(["--data-dir", str(data_dir), "--dry-run", "--seed", SEED]) == 0
    assert file_bytes(data_dir) == before


def test_verify_refuses_a_truncated_dataset(rebalance, tmp_path):
    """``--verify`` refuses a manifest that cannot prove content preservation."""
    data_dir = write_truncated_dataset(tmp_path / "data")
    manifest_path = tmp_path / "manifest.json"

    assert migrate(rebalance, data_dir, manifest_path, allow_truncated=True) == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["truncated_option_count"] == 1
    assert manifest["content_preservation_valid"] is False

    assert rebalance.main(["--data-dir", str(data_dir), "--verify", str(manifest_path)]) == 1
    assert rebalance.main(
        ["--data-dir", str(data_dir), "--verify", str(manifest_path), "--allow-truncated"]
    ) == 0


def test_verify_refuses_a_dataset_that_is_truncated_now(rebalance, tmp_path):
    """``--verify`` refuses data that carries truncated options."""
    data_dir = write_dataset(tmp_path / "data", build_questions(6))
    manifest_path = tmp_path / "manifest.json"
    assert migrate(rebalance, data_dir, manifest_path) == 0

    # The data is truncated after the migration.
    path = data_dir / "drupal_quiz_001.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "- Option A of question 1", "- Option A of question 1 #a trailing comment"
        ),
        encoding="utf-8",
    )

    assert rebalance.main(["--data-dir", str(data_dir), "--verify", str(manifest_path)]) == 1


def test_multi_line_option_source_is_preserved(rebalance, tmp_path):
    """A multi-line option keeps its tail text when the file is rewritten."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "drupal_quiz_001.yaml").write_text(
        "id: drupal_quiz_001\n"
        "question: Question 1?\n"
        "choices:\n"
        "  - A) Set a non-zero\n"
        "    max-age and declare needed contexts #cache['max-age'] = 0\n"
        "  - B) Second option\n"
        "  - C) Third option\n"
        "  - D) Fourth option\n"
        "  - E) Fifth option\n"
        "  - F) Sixth option\n"
        "correct_choice: A\n",
        encoding="utf-8",
    )
    before = yaml.safe_load((data_dir / "drupal_quiz_001.yaml").read_text(encoding="utf-8"))
    assert before["choices"][0] == "A) Set a non-zero max-age and declare needed contexts"

    assert migrate(rebalance, data_dir, allow_truncated=True) == 0

    text = (data_dir / "drupal_quiz_001.yaml").read_text(encoding="utf-8")
    # The tail text is still in the file ...
    assert "max-age and declare needed contexts #cache['max-age'] = 0" in text
    # ... the file is still valid YAML ...
    after = yaml.safe_load(text)
    assert len(after["choices"]) == 6
    # ... and the option text did not lose anything.
    assert after["choices"][0] == "Set a non-zero max-age and declare needed contexts"

