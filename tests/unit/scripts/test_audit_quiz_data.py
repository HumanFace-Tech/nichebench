"""Unit tests for scripts/audit_quiz_data.py.

The audit is read-only: every scenario builds a small synthetic dataset in
``tmp_path`` and never touches the real quiz dataset.

Coverage:
- duplicate choices are detected on the normalized text (the letter prefixes
  hide duplicates),
- truncated options (an unquoted ``#`` starts a YAML comment) are reported
  with the parsed text and the authored text up to the comment,
- the audit reports everything it finds and exits non-zero only for genuine
  data-contract violations; advisory findings (missing optional ``context``,
  repeated bare question stems) keep the exit status at 0,
- the committed CI fixture dataset audits clean, so the CI data gate fails
  for real defects rather than for the fixture.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_PATH = REPO_ROOT / "scripts" / "audit_quiz_data.py"
CI_DATASET_DIR = REPO_ROOT / "tests" / "fixtures" / "quiz" / "ci_dataset"


def _load_module():
    """Load the script by path; it lives outside the package on purpose."""
    spec = importlib.util.spec_from_file_location("audit_quiz_data", AUDIT_PATH)
    assert spec and spec.loader, "audit_quiz_data.py must exist and be importable"
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_quiz_data"] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("audit_quiz_data", None)


@pytest.fixture(scope="module")
def audit_module():
    return _load_module()


def write_quiz(root: Path, name: str, choices: List[str], correct: str = "A") -> Path:
    """Write one quiz file with the given choice lines."""
    root.mkdir(parents=True, exist_ok=True)
    body = "".join(f"  - {choice}\n" for choice in choices)
    (root / name).write_text(
        "id: drupal_quiz_001\n"
        "context: Some context.\n"
        "summary: Some summary.\n"
        "question: Question 1?\n"
        "choices:\n"
        f"{body}"
        f"correct_choice: {correct}\n",
        encoding="utf-8",
    )
    return root


def test_duplicate_choices_are_detected_on_the_normalized_text(audit_module, tmp_path):
    """``A) Set`` and ``B) Set`` are the same option once the prefixes are gone."""
    data_dir = write_quiz(
        tmp_path / "data",
        "quiz_001.yaml",
        ["A) Set", "B) Set", "C) Other option", "D) Another option"],
    )

    report = audit_module.audit(data_dir)

    assert report["duplicate_choices_as_stored"] == []
    normalized = report["duplicate_choices_normalized"]
    assert len(normalized) == 1
    assert normalized[0]["file"] == "quiz_001.yaml"
    assert normalized[0]["choices"] == ["Set"]


def test_parenthesised_prefixes_are_detected_by_the_canonical_predicate(audit_module, tmp_path):
    """``(A) Option`` is a prefix under the canonical ``quiz_text`` predicate.

    ``normalize_choice`` strips it, so ``normalized_choices_sha256`` differs
    from ``choices_sha256`` — the prefix counters and the ``letter_prefix``
    violation have to agree with that.  The audit used to keep its own prefix
    regex, which missed the optional parenthesis and reported such a file as
    prefix-free.
    """
    data_dir = write_quiz(
        tmp_path / "data",
        "quiz_001.yaml",
        ["(A) First option", "(B) Second option", "(C) Third option", "(D) Fourth option"],
    )

    report = audit_module.audit(data_dir)

    assert report["prefixed_choice_count"] == 4
    assert report["files_with_prefixed_choices"] == 1
    assert report["files_with_all_choices_prefixed"] == 1
    assert [entry["kind"] for entry in report["violations"]] == ["letter_prefix"]
    # The counters agree with the normalization the hashes are built on.
    entry = report["files"][0]
    assert entry["choices_sha256"] != entry["normalized_choices_sha256"]
    assert audit_module.main(["--data-dir", str(data_dir)]) == 1


def test_truncated_options_are_reported(audit_module, tmp_path):
    """An option cut short by a YAML comment is reported with its source."""
    data_dir = write_quiz(
        tmp_path / "data",
        "quiz_001.yaml",
        [
            "A) Set #cache['max-age'] = 0 so caching is respected.",
            "B) Second option",
            "C) Third option",
            "D) Fourth option",
        ],
    )

    report = audit_module.audit(data_dir)

    assert report["truncated_option_count"] == 1
    entry = report["truncated_options"][0]
    assert entry["file"] == "quiz_001.yaml"
    assert entry["index"] == 0
    assert entry["parsed"] == "Set"
    assert entry["authored_prefix"] == "Set"
    assert "cache['max-age']" in entry["comment"]
    # The authored text is reported, never reworded.
    assert entry["authored"] == "Set #cache['max-age'] = 0 so caching is respected."


def test_a_clean_dataset_reports_no_defects(audit_module, tmp_path):
    """A dataset with plain, distinct options has no defects."""
    data_dir = write_quiz(
        tmp_path / "data",
        "quiz_001.yaml",
        ["First option", "Second option", "Third option", "Fourth option"],
    )

    report = audit_module.audit(data_dir)

    assert report["truncated_option_count"] == 0
    assert report["truncated_options"] == []
    assert report["duplicate_choices_normalized"] == []


def test_the_audit_fails_on_contract_violations(audit_module, tmp_path, capsys):
    """A dataset that violates the contract exits 1 — and still prints its report.

    The audit doubles as the CI data gate: reporting defects while exiting 0
    made the gate a no-op.
    """
    data_dir = write_quiz(
        tmp_path / "data",
        "quiz_001.yaml",
        [
            "A) Set #cache['max-age'] = 0 so caching is respected.",
            "B) Set",
            "C) Third option",
            "D) Fourth option",
        ],
    )

    exit_code = audit_module.main(["--data-dir", str(data_dir)])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "truncated options" in output
    assert "duplicate choices (normalized)" in output
    assert "audit result          : FAIL" in output


def test_a_clean_dataset_exits_zero(audit_module, tmp_path):
    """A dataset that satisfies the contract exits 0."""
    data_dir = write_quiz(
        tmp_path / "data",
        "quiz_001.yaml",
        ["First option", "Second option", "Third option", "Fourth option"],
    )

    assert audit_module.main(["--data-dir", str(data_dir)]) == 0


def test_advisory_findings_do_not_fail_the_audit(audit_module, tmp_path):
    """Optional ``context`` and repeated bare question stems are advisory.

    Different items may share a stem such as "What should you do?" as long as
    their context/summary differ, and ``context`` is not required by the
    contract — neither may turn the gate red.
    """
    root = tmp_path / "data"
    root.mkdir(parents=True)
    (root / "quiz_001.yaml").write_text(
        "id: drupal_quiz_001\n"
        "summary: Caching.\n"
        "question: What should you do?\n"
        "choices:\n"
        "  - First option\n"
        "  - Second option\n"
        "correct_choice: A\n",
        encoding="utf-8",
    )
    (root / "quiz_002.yaml").write_text(
        "id: drupal_quiz_002\n"
        "summary: Tagging.\n"
        "question: What should you do?\n"
        "choices:\n"
        "  - First option\n"
        "  - Second option\n"
        "correct_choice: B\n",
        encoding="utf-8",
    )

    report = audit_module.audit(root)

    assert report["missing_fields"]["context"] == 2
    assert len(report["duplicate_question_hashes"]) == 1
    assert report["violations"] == []
    assert audit_module.main(["--data-dir", str(root)]) == 0


@pytest.mark.parametrize(
    ("choices", "correct", "kind"),
    [
        (["Only one option"], "A", "choice_count"),
        (["First", "Second", "Third", "Fourth"], "Z", "invalid_gold"),
        (["First", "Second", "Third"], "D", "invalid_gold"),
    ],
)
def test_unscorable_items_are_contract_violations(audit_module, tmp_path, choices, correct, kind):
    """A choice count outside the contract or a gold letter out of range fails."""
    data_dir = write_quiz(tmp_path / "data", "quiz_001.yaml", choices, correct=correct)

    report = audit_module.audit(data_dir)

    assert [entry["kind"] for entry in report["violations"]] == [kind]


def test_a_missing_required_field_is_a_contract_violation(audit_module, tmp_path):
    """The contract requires id, question, choices and correct_choice."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "quiz_001.yaml").write_text(
        "context: Some context.\n"
        "summary: Some summary.\n"
        "question: Question 1?\n"
        "choices:\n"
        "  - First option\n"
        "  - Second option\n",
        encoding="utf-8",
    )

    report = audit_module.audit(data_dir)

    kinds = {(entry["kind"], entry["detail"]) for entry in report["violations"]}
    assert ("missing_field", "missing 'id'") in kinds
    assert ("missing_field", "missing 'correct_choice'") in kinds


@pytest.mark.parametrize(
    "gold_line",
    [
        "correct_choice:\n",  # null value
        "correct_choice: ''\n",  # empty string
        "correct_choice: '   '\n",  # whitespace-only string
    ],
)
def test_blank_gold_is_a_missing_field_violation(audit_module, tmp_path, gold_line):
    """A blank gold leaves the item unscorable, so the data gate must fail.

    The contract test rejects all three forms (``gold.upper()`` is not a
    letter), so the audit reports them as ``missing_field`` — never as
    ``invalid_gold`` and never as a silent pass.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "quiz_001.yaml").write_text(
        "id: drupal_quiz_001\n"
        "context: Some context.\n"
        "summary: Some summary.\n"
        "question: Question 1?\n"
        "choices:\n"
        "  - First option\n"
        "  - Second option\n"
        f"{gold_line}",
        encoding="utf-8",
    )

    report = audit_module.audit(data_dir)

    assert [(entry["kind"], entry["detail"]) for entry in report["violations"]] == [
        ("missing_field", "missing 'correct_choice'")
    ]
    assert report["missing_fields"]["correct_choice"] == 1
    assert report["files"][0]["gold"] == ""
    assert audit_module.main(["--data-dir", str(data_dir)]) == 1


def test_a_duplicate_item_identity_is_a_contract_violation(audit_module, tmp_path):
    """Two items with the same question + context + summary are duplicates."""
    root = tmp_path / "data"
    root.mkdir(parents=True)
    body = (
        "context: Some context.\n"
        "summary: Some summary.\n"
        "question: What should you do?\n"
        "choices:\n"
        "  - First option\n"
        "  - Second option\n"
        "correct_choice: A\n"
    )
    (root / "quiz_001.yaml").write_text("id: drupal_quiz_001\n" + body, encoding="utf-8")
    (root / "quiz_002.yaml").write_text("id: drupal_quiz_002\n" + body, encoding="utf-8")

    report = audit_module.audit(root)

    duplicate_kinds = [entry["kind"] for entry in report["violations"] if entry["kind"] == "duplicate_item"]
    assert sorted(duplicate_kinds) == ["duplicate_item", "duplicate_item"]


def test_the_report_keeps_its_backward_compatible_shape(audit_module, tmp_path):
    """The JSON report keeps the keys it had and adds the new ones."""
    data_dir = write_quiz(
        tmp_path / "data",
        "quiz_001.yaml",
        ["First option", "Second option", "Third option", "Fourth option"],
    )

    report: Dict[str, Any] = audit_module.audit(data_dir)

    for key in (
        "data_dir",
        "file_count",
        "parsed_file_count",
        "total_choices",
        "choices_per_file",
        "gold_distribution",
        "prefixed_choice_count",
        "non_string_choice_count",
        "missing_fields",
        "duplicate_choice_files",
        "duplicate_question_hashes",
        "duplicate_item_hashes",
        "files",
        "violation_count",
        "violations",
    ):
        assert key in report, f"the report lost the key {key!r}"
    for key in ("duplicate_choices_as_stored", "duplicate_choices_normalized", "truncated_options"):
        assert key in report, f"the report is missing the key {key!r}"
    assert report["violation_count"] == len(report["violations"])


def test_the_ci_fixture_dataset_is_clean(audit_module):
    """The CI fixture dataset is what the CI data gate actually audits.

    CI checks out without the private data submodule, so the audit step runs
    against ``tests/fixtures/quiz/ci_dataset``.  The fixture must hold no
    defects, otherwise the gate would pass or fail for the wrong reason.
    """
    assert CI_DATASET_DIR.is_dir(), f"CI fixture dataset missing at {CI_DATASET_DIR}"

    report = audit_module.audit(CI_DATASET_DIR)

    assert report["file_count"] == 12
    assert report["parsed_file_count"] == 12
    assert report["parse_errors"] == []
    assert report["total_choices"] == 72
    assert report["choices_per_file"] == {"6": 12}
    assert report["prefixed_choice_count"] == 0
    assert report["non_string_choice_count"] == 0
    assert report["truncated_option_count"] == 0
    assert report["duplicate_choices_normalized"] == []
    assert report["duplicate_choices_as_stored"] == []
    assert report["duplicate_question_hashes"] == {}
    assert report["duplicate_item_hashes"] == {}
    assert report["missing_fields"] == {
        "context": 0,
        "summary": 0,
        "question": 0,
        "correct_choice": 0,
    }
    assert report["gold_distribution"] == {"A": 2, "B": 2, "C": 2, "D": 2, "E": 2, "F": 2}
    assert report["violations"] == []
    assert report["violation_count"] == 0
    assert audit_module.main(["--data-dir", str(CI_DATASET_DIR)]) == 0
