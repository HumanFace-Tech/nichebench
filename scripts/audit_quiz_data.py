#!/usr/bin/env python3
"""Read-only audit of the quiz dataset.

Reports the shape of the dataset and the data-contract violations that the
quiz-v2 migration has to close.  The script never writes to the data
directory; the only file it can create is the optional JSON report.

Exit status
===========
``0`` when every item satisfies the data contract, ``1`` when the dataset
carries contract violations (or the data dir is missing).  The contract is
the one ``tests/unit/frameworks/test_quiz_data_contract.py`` asserts: the
required fields — a blank ``correct_choice`` (absent, empty or
whitespace-only) leaves the item unscorable and is reported as a
``missing_field`` violation, never as ``invalid_gold`` — 2-6 choices, a
non-blank ``correct_choice`` inside the available range (a non-blank letter
outside that range is ``invalid_gold``), non-empty options without
redundant letter prefixes, no repeated option text inside one question, no
repeated item identity across the dataset, and no option truncated by a
YAML comment.  Advisory statistics — the gold distribution, the bare
question-stem repeats (different items may share a stem), mapping-typed
options the loader renders, and files missing an optional ``context`` —
are reported but never fail the audit.

Dependencies: standard library plus PyYAML.  No network access.

Canonicalisation used here (referenced by later migration tooling):

* ``question_hash`` - SHA-256 of the question text lower-cased with every run
  of whitespace collapsed to a single space.
* ``item_hash`` - SHA-256 of question + context + summary, all normalised the
  same way.  This identifies an item independently of its options, so
  reordering options or stripping letter prefixes does not change it.
* ``choices_sha256`` - SHA-256 of the exact choice strings, sorted
  lexicographically and joined with ``"\\n"`` (UTF-8).
* ``normalized_choices_sha256`` - the same, but every choice has its redundant
  leading letter prefix removed first.  This is the hash that stays stable when
  options are reordered and de-prefixed, so it is the one to compare across a
  migration.
* letter prefixes are detected with
  ``nichebench.core.quiz_text.has_choice_prefix`` — the same operation that
  strips them for ``normalize_choice`` — so the prefix counters cannot
  disagree with ``normalized_choices_sha256``.

Duplicate choices are reported twice: ``duplicate_choices_as_stored`` looks at
the text as it is stored (the letter prefixes hide duplicates) and
``duplicate_choices_normalized`` looks at the text without the prefixes,
which is what the model actually sees.

Truncated options are reported as well: an unquoted ``#`` starts a YAML
comment, so the option text the harness sees is cut short.  The audit reports
the parsed text and the authored text up to the comment, without rewording
anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

try:  # the package is installed in the development environment
    from nichebench.core.quiz_text import (
        find_truncated_options,
        has_choice_prefix,
        normalize_choice,
        read_choice_sources,
        render_choice,
    )
except ModuleNotFoundError:  # pragma: no cover - plain checkout without install
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from nichebench.core.quiz_text import (
        find_truncated_options,
        has_choice_prefix,
        normalize_choice,
        read_choice_sources,
        render_choice,
    )

REQUIRED_FIELDS = ("context", "summary", "question", "correct_choice")
GOLD_LETTERS = ("A", "B", "C", "D", "E", "F")

# Fields the data contract requires in every item (the contract lives in
# ``tests/unit/frameworks/test_quiz_data_contract.py``).  ``context`` and
# ``summary`` stay out on purpose: they feed the item hash but a dataset may
# legitimately omit them, so their absence is reported, never failed.
# ``choices`` is validated separately below, because an empty list is a
# count problem rather than a missing field.
CONTRACT_REQUIRED_FIELDS = ("id", "question", "choices", "correct_choice")

# Choice-count bounds of the contract.
MIN_CHOICES = 2
MAX_CHOICES = 6

DEFAULT_DATA_DIR = Path("src/nichebench/frameworks/drupal/data/quiz")


def normalize_question(text: Any) -> str:
    """Lower-case the question and collapse whitespace runs to single spaces."""
    return " ".join(str(text or "").lower().split())


def item_hash(data: Dict[str, Any]) -> str:
    """Identity hash of a quiz item.

    The hash covers the question together with its context and summary. Bare
    question stems do repeat in the private dataset (several items share a
    stem such as "What should you do?"), while no two items share the same
    context. The options are excluded on purpose: reordering options or
    stripping letter prefixes must not change the identity of an item.
    """
    payload = "|".join(
        (
            normalize_question(data.get("question")),
            normalize_question(data.get("context")),
            normalize_question(data.get("summary")),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def choice_multiset_sha256(choices: List[str], strip_prefixes: bool = False) -> str:
    """SHA-256 of the sorted choice multiset, joined by newlines."""
    values = [normalize_choice(choice) if strip_prefixes else str(choice) for choice in choices]
    payload = "\n".join(sorted(values)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def count_prefixed(choices: List[str]) -> int:
    """Number of choices that start with a redundant letter marker.

    Uses the canonical predicate from ``nichebench.core.quiz_text`` — the
    very operation ``normalize_choice`` applies — so a file can never be
    counted as prefix-free while its ``normalized_choices_sha256`` differs
    from its ``choices_sha256``.
    """
    return sum(1 for choice in choices if has_choice_prefix(choice))


def audit(data_dir: Path) -> Dict[str, Any]:
    """Audit every ``*.yaml`` file in ``data_dir`` and return a report.

    The report carries every statistic it always did plus ``violations`` (and
    its count): the contract violations that make :func:`main` exit non-zero.
    Advisory findings — the gold distribution, bare question-stem repeats,
    mapping-typed options, files missing an optional ``context`` — are
    reported but never listed as violations.
    """
    files = sorted(data_dir.glob("*.yaml"))

    choices_per_file: Counter = Counter()
    gold_distribution: Counter = Counter()
    missing_fields: Counter = Counter()
    duplicate_choice_files: List[Dict[str, Any]] = []
    duplicate_choice_files_normalized: List[Dict[str, Any]] = []
    duplicate_question_hashes: Dict[str, List[str]] = {}
    duplicate_item_hashes: Dict[str, List[str]] = {}
    truncated_options: List[Dict[str, Any]] = []
    per_file: List[Dict[str, Any]] = []
    parse_errors: List[Dict[str, str]] = []
    violations: List[Dict[str, Any]] = []

    total_choices = 0
    prefixed_choices = 0
    files_with_prefixed_choices = 0
    files_with_all_choices_prefixed = 0
    non_string_choices = 0

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001 - a broken file must not abort the audit
            parse_errors.append({"file": path.name, "error": str(exc)})
            violations.append({"file": path.name, "kind": "parse_error", "detail": str(exc)})
            continue
        if not isinstance(data, dict):
            parse_errors.append({"file": path.name, "error": "file did not parse to a mapping"})
            violations.append({"file": path.name, "kind": "parse_error", "detail": "file did not parse to a mapping"})
            continue

        raw_choices = data.get("choices")
        raw_values = list(raw_choices) if isinstance(raw_choices, list) else []
        choices = [render_choice(value) for value in raw_values]
        normalized = [normalize_choice(value) for value in raw_values]

        total_choices += len(choices)
        choices_per_file[len(choices)] += 1
        non_string_choices += sum(1 for value in raw_values if not isinstance(value, str))

        file_prefixed = count_prefixed(choices)
        prefixed_choices += file_prefixed
        if file_prefixed:
            files_with_prefixed_choices += 1
        if choices and file_prefixed == len(choices):
            files_with_all_choices_prefixed += 1

        gold = str(data.get("correct_choice") or "").strip().upper()
        if gold:
            gold_distribution[gold] += 1
        # A blank gold (absent, empty or whitespace-only) leaves the item
        # unscorable, so the contract reports it as a missing required field.
        # Only a non-blank gold can be out of range (``invalid_gold``).
        gold_missing = not gold

        file_missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
        if gold_missing and "correct_choice" not in file_missing:
            file_missing.append("correct_choice")
        for field in file_missing:
            missing_fields[field] += 1

        duplicates = sorted({choice for choice in choices if choices.count(choice) > 1})
        if duplicates:
            duplicate_choice_files.append({"file": path.name, "choices": duplicates})

        # The letter prefixes hide duplicates: the model sees the text without
        # them, so the same check has to run on the normalized text.
        normalized_duplicates = sorted({choice for choice in normalized if normalized.count(choice) > 1})
        if normalized_duplicates:
            duplicate_choice_files_normalized.append({"file": path.name, "choices": normalized_duplicates})

        sources = read_choice_sources(text) or []
        file_truncated = find_truncated_options(raw_values, sources)
        for item in file_truncated:
            truncated_options.append({"file": path.name, **item})

        # Contract violations: the conditions
        # ``tests/unit/frameworks/test_quiz_data_contract.py`` asserts.  Each
        # one makes the item unusable or ambiguous for the model, so the audit
        # exits non-zero when any of them is present.
        contract_missing = [
            field for field in CONTRACT_REQUIRED_FIELDS if field != "choices" and not data.get(field)
        ]
        if gold_missing and "correct_choice" not in contract_missing:
            contract_missing.append("correct_choice")
        for field in contract_missing:
            violations.append({"file": path.name, "kind": "missing_field", "detail": f"missing {field!r}"})
        if "choices" not in data or raw_choices is None:
            violations.append({"file": path.name, "kind": "missing_field", "detail": "missing 'choices'"})
        elif not isinstance(raw_choices, list):
            violations.append({"file": path.name, "kind": "missing_field", "detail": "'choices' is not a list"})
        elif not (MIN_CHOICES <= len(choices) <= MAX_CHOICES):
            violations.append(
                {
                    "file": path.name,
                    "kind": "choice_count",
                    "detail": f"{len(choices)} choices (expected {MIN_CHOICES}-{MAX_CHOICES})",
                }
            )
        empty_choices = [index for index, choice in enumerate(choices) if not choice.strip()]
        if empty_choices:
            violations.append(
                {"file": path.name, "kind": "empty_choice", "detail": f"empty choice(s) at index {empty_choices}"}
            )
        if gold and len(choices) >= MIN_CHOICES and gold not in GOLD_LETTERS[: len(choices)]:
            violations.append(
                {
                    "file": path.name,
                    "kind": "invalid_gold",
                    "detail": f"correct_choice {gold!r} outside A-{GOLD_LETTERS[len(choices) - 1]}",
                }
            )
        if file_prefixed:
            violations.append(
                {
                    "file": path.name,
                    "kind": "letter_prefix",
                    "detail": f"{file_prefixed} choice(s) carry a redundant letter prefix",
                }
            )
        if normalized_duplicates:
            violations.append(
                {
                    "file": path.name,
                    "kind": "duplicate_choices",
                    "detail": ", ".join(normalized_duplicates),
                }
            )
        for item in file_truncated:
            violations.append(
                {
                    "file": path.name,
                    "kind": "truncated_option",
                    "detail": f"option {item['index']} cut short by a YAML comment",
                }
            )

        question_hash = hashlib.sha256(normalize_question(data.get("question")).encode("utf-8")).hexdigest()
        duplicate_question_hashes.setdefault(question_hash, []).append(path.name)

        digest = item_hash(data)
        duplicate_item_hashes.setdefault(digest, []).append(path.name)

        per_file.append(
            {
                "file": path.name,
                "id": str(data.get("id") or path.stem),
                "choice_count": len(choices),
                "gold": gold,
                "prefixed_choices": file_prefixed,
                "non_string_choices": sum(1 for value in raw_values if not isinstance(value, str)),
                "missing_fields": file_missing,
                "question_hash": question_hash,
                "item_hash": digest,
                "choices_sha256": choice_multiset_sha256(choices),
                "normalized_choices_sha256": choice_multiset_sha256(choices, strip_prefixes=True),
                "truncated_option_count": len(file_truncated),
            }
        )

    duplicate_question_hashes = {
        question_hash: names
        for question_hash, names in duplicate_question_hashes.items()
        if len(names) > 1
    }
    duplicate_item_hashes = {
        digest: names for digest, names in duplicate_item_hashes.items() if len(names) > 1
    }
    for names in duplicate_item_hashes.values():
        for name in names:
            others = ", ".join(other for other in names if other != name)
            violations.append(
                {"file": name, "kind": "duplicate_item", "detail": f"same item identity as {others}"}
            )

    return {
        "data_dir": str(data_dir),
        "file_count": len(files),
        "parsed_file_count": len(per_file),
        "parse_errors": parse_errors,
        "total_choices": total_choices,
        "choices_per_file": {str(count): n for count, n in sorted(choices_per_file.items())},
        "gold_distribution": {letter: gold_distribution.get(letter, 0) for letter in GOLD_LETTERS},
        "gold_e_count": gold_distribution.get("E", 0),
        "gold_f_count": gold_distribution.get("F", 0),
        "prefixed_choice_count": prefixed_choices,
        "files_with_prefixed_choices": files_with_prefixed_choices,
        "files_with_all_choices_prefixed": files_with_all_choices_prefixed,
        "non_string_choice_count": non_string_choices,
        "missing_fields": {field: missing_fields.get(field, 0) for field in REQUIRED_FIELDS},
        "duplicate_choice_files": duplicate_choice_files,
        "duplicate_choices_as_stored": duplicate_choice_files,
        "duplicate_choices_normalized": duplicate_choice_files_normalized,
        "duplicate_question_hashes": duplicate_question_hashes,
        "duplicate_item_hashes": duplicate_item_hashes,
        "truncated_option_count": len(truncated_options),
        "truncated_options": truncated_options,
        "violation_count": len(violations),
        "violations": violations,
        "files": per_file,
    }


def render_summary(report: Dict[str, Any]) -> str:
    """Render a human-readable summary of an audit report."""
    lines: List[str] = []
    lines.append("Quiz data audit")
    lines.append("=" * 60)
    lines.append(f"data dir              : {report['data_dir']}")
    lines.append(f"files                 : {report['file_count']} (parsed {report['parsed_file_count']})")

    per_file = ", ".join(f"{count} choices x {n} files" for count, n in report["choices_per_file"].items())
    lines.append(f"choices per file      : {per_file or 'n/a'}")
    lines.append(f"choices total         : {report['total_choices']}")

    gold = " ".join(f"{letter}={count}" for letter, count in report["gold_distribution"].items())
    lines.append(f"gold distribution     : {gold}")
    lines.append(f"gold E / F counts     : {report['gold_e_count']} / {report['gold_f_count']}")

    lines.append(
        f"prefixed choices      : {report['prefixed_choice_count']} / {report['total_choices']}"
        f" (in {report['files_with_prefixed_choices']} files,"
        f" {report['files_with_all_choices_prefixed']} fully prefixed)"
    )
    lines.append(f"non-string choices    : {report['non_string_choice_count']} (YAML parsed a mapping)")

    missing = " ".join(f"{field}={count}" for field, count in report["missing_fields"].items())
    lines.append(f"missing fields        : {missing}")

    duplicate_choices = len(report["duplicate_choice_files"])
    lines.append(f"duplicate choices     : {duplicate_choices} file(s) with repeated choice text")
    lines.append(
        f"duplicate choices (normalized): {len(report['duplicate_choices_normalized'])} file(s)"
        " with repeated choice text after the letter prefixes are stripped"
    )
    for entry in report["duplicate_choices_normalized"]:
        lines.append(f"    {entry['file']}: {entry['choices']}")

    truncated = report["truncated_option_count"]
    lines.append(f"truncated options     : {truncated} option(s) cut short by a YAML comment")
    for entry in report["truncated_options"]:
        lines.append(f"    {entry['file']} [{entry['index']}] parsed={entry['parsed']!r}")
        lines.append(f"        authored prefix={entry['authored_prefix']!r}")

    duplicate_hashes = report["duplicate_question_hashes"]
    duplicated_files = sum(len(names) for names in duplicate_hashes.values())
    lines.append(
        f"duplicate question hashes: {len(duplicate_hashes)} group(s) over {duplicated_files} file(s)"
        " (advisory: bare stems may repeat)"
    )
    for question_hash, names in sorted(duplicate_hashes.items(), key=lambda item: item[1][0]):
        lines.append(f"    {question_hash[:12]}  {', '.join(names)}")

    duplicate_items = report["duplicate_item_hashes"]
    lines.append(f"duplicate item hashes  : {len(duplicate_items)} group(s) (question + context + summary)")

    if report["parse_errors"]:
        lines.append("parse errors:")
        for error in report["parse_errors"]:
            lines.append(f"    {error['file']}: {error['error']}")

    violations = report.get("violations") or []
    lines.append(f"contract violations   : {len(violations)}")
    for entry in violations[:20]:
        lines.append(f"    {entry['file']}: {entry['kind']} - {entry['detail']}")
    if len(violations) > 20:
        lines.append(f"    ... and {len(violations) - 20} more")

    lines.append("")
    lines.append(f"audit result          : {'FAIL' if violations else 'PASS'}")
    if violations:
        lines.append("a non-zero exit status follows while contract violations remain")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit the quiz dataset (read-only).")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory holding the quiz YAML files",
    )
    parser.add_argument("--json-out", type=Path, default=None, help="Optional path for the JSON report")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.data_dir.is_dir():
        print(f"error: data dir does not exist: {args.data_dir}", file=sys.stderr)
        return 1

    report = audit(args.data_dir)
    print(render_summary(report))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\nwrote JSON report to {args.json_out}")

    # The audit doubles as the CI data gate: a dataset that violates the
    # contract must fail the run, a dataset that only carries advisory
    # findings must not.
    return 1 if report["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
