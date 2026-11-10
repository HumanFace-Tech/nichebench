#!/usr/bin/env python3
"""Normalize and rebalance the quiz dataset for ``quiz-v2``.

The script performs the one-time data migration described in
``tasks_quiz_version_two.md`` (Q1):

1. every option loses its redundant leading letter marker (``A) ``, ``A. ``,
   ``A: ``, ``A - ``, ``(A) ``),
2. the gold option is moved to a balanced position so that no letter is
   over-represented,
3. ``dataset_version: quiz-v2`` is stamped on every quiz.

Only two things change in the data: the *order* of the options and the
redundant letter prefixes.  Option texts are never invented, reworded or
dropped - a SHA-256 hash of the sorted option multiset is written to the
manifest before and after the migration so that content preservation is
provable.

Two dataset quirks are handled explicitly:

* Some options contain an unquoted ``key: value`` and therefore parse as a
  YAML mapping.  They are rendered back to ``key: value`` (same helper as
  ``scripts/audit_quiz_data.py``) and re-emitted as quoted strings.  Never
  call ``str(dict)`` on them.
* Some options contain ``" #"``, which YAML treats as the start of a
  comment, so the *parsed* option text is truncated ("Set a non-zero"
  instead of "Set a non-zero #cache['max-age'] ...").  Such options make the
  migration unsafe: rewriting the file would drop the text behind the
  ``#`` and the manifest could no longer claim that the option text was
  preserved.  ``--write`` therefore refuses to run while the dataset
  contains truncated options; the affected files and options are listed
  with the authored line that has to be quoted.  ``--allow-truncated``
  overrides the check and exists for tests only.

Dependencies: standard library plus PyYAML.  No network access.

Usage::

    # inspect only
    python scripts/quiz_v2_rebalance.py --data-dir DIR --dry-run \\
        --seed quiz-v2-2026-11 --manifest reports/quiz_v2_manifest.json

    # apply (requires approval for the private data submodule)
    python scripts/quiz_v2_rebalance.py --data-dir DIR --write \\
        --seed quiz-v2-2026-11 --manifest reports/quiz_v2_manifest.json

    # prove that the data still matches the manifest
    python scripts/quiz_v2_rebalance.py --data-dir DIR \\
        --verify reports/quiz_v2_manifest.json
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import random
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

try:  # the package is installed in the development environment
    from nichebench.core.quiz_text import (
        authored_text,
        find_truncated_options,
        normalize_choice,
        read_choice_sources,
        render_choice,
        strip_choice_prefix,
    )
except ModuleNotFoundError:  # pragma: no cover - plain checkout without install
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from nichebench.core.quiz_text import (
        authored_text,
        find_truncated_options,
        normalize_choice,
        read_choice_sources,
        render_choice,
        strip_choice_prefix,
    )

DATASET_VERSION = "quiz-v2"
DEFAULT_DATA_DIR = Path("src/nichebench/frameworks/drupal/data/quiz")
DEFAULT_SEED = "quiz-v2-2026-11"

GOLD_LETTERS = "ABCDEF"
MIN_CHOICES = 2
MAX_CHOICES = 6

# How many changed files are shown as a unified diff in the dry-run report.
DIFF_EXAMPLES = 3


class QuizDataError(ValueError):
    """Raised when a quiz file does not satisfy the quiz-v2 data contract."""


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

# The choice rendering lives in ``nichebench.core.quiz_text`` so that the
# audit, the rebalance and the loader agree on how a choice is displayed.
# ``choice_text`` is kept as an alias for readability.
choice_text = render_choice


def multiset_sha256(choices: Sequence[str]) -> str:
    """SHA-256 of the sorted choice multiset, joined by newlines."""
    payload = "\n".join(sorted(choices)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Source text helpers
# ---------------------------------------------------------------------------
#
# ``read_choice_sources`` and ``authored_text`` live in
# ``nichebench.core.quiz_text`` so that the audit, the rebalance and the
# loader read the dataset the same way.


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class QuizTask:
    """One quiz file, with the information the migration needs."""

    path: Path
    data: Dict[str, Any]
    task_id: str
    question: str
    choices: List[str]
    old_correct: str
    old_correct_idx: int
    sources: List[Optional[str]] = field(default_factory=list)
    truncated: List[Dict[str, Any]] = field(default_factory=list)
    authored: List[str] = field(default_factory=list)
    new_correct: str = ""
    new_choices: List[str] = field(default_factory=list)
    new_sources: List[Optional[str]] = field(default_factory=list)

    @property
    def n_choices(self) -> int:
        return len(self.choices)

    @property
    def authored_sha256(self) -> Optional[str]:
        """SHA-256 of the sorted authored option texts, when they are known.

        The hash covers the text the options were *authored* with, not the
        text YAML parsed.  It is the only content-preservation proof that
        survives a truncated option.
        """
        if not self.authored:
            return None
        return multiset_sha256(self.authored)


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def load_task(path: Path) -> QuizTask:
    """Load, validate and normalize a single quiz file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QuizDataError(f"{path.name}: could not be read: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - a broken file must be reported, not raised raw
        raise QuizDataError(f"{path.name}: could not be parsed: {exc}") from exc

    if not isinstance(data, dict):
        raise QuizDataError(f"{path.name}: file did not parse to a mapping")

    task_id = str(data.get("id") or "").strip()
    if not task_id:
        raise QuizDataError(f"{path.name}: missing 'id'")

    question = str(data.get("question") or "").strip()
    if not question:
        raise QuizDataError(f"{path.name}: missing 'question'")

    raw_choices = data.get("choices")
    if not isinstance(raw_choices, list):
        raise QuizDataError(f"{path.name}: 'choices' must be a list")
    if not MIN_CHOICES <= len(raw_choices) <= MAX_CHOICES:
        raise QuizDataError(
            f"{path.name}: expected {MIN_CHOICES}-{MAX_CHOICES} choices, found {len(raw_choices)}"
        )

    normalized = [normalize_choice(value) for value in raw_choices]

    correct = str(data.get("correct_choice") or "").strip().upper()
    if len(correct) != 1 or correct not in GOLD_LETTERS:
        raise QuizDataError(f"{path.name}: 'correct_choice' must be a single letter A-F")
    correct_idx = GOLD_LETTERS.index(correct)
    if correct_idx >= len(normalized):
        raise QuizDataError(
            f"{path.name}: 'correct_choice' {correct} is out of range for {len(normalized)} choices"
        )

    # The authored source text is used to tell a genuine duplicate option
    # apart from an option that only looks like a duplicate because YAML
    # truncated it at a comment.
    sources = read_choice_sources(text) or []
    if len(sources) != len(raw_choices):
        sources = []
    source_normalized = [strip_choice_prefix(authored_text(item)) for item in sources]

    truncated = find_truncated_options(raw_choices, sources)
    kept_sources: List[Optional[str]] = [None] * len(normalized)
    for item in truncated:
        # YAML cut the option at a comment.  The source line is kept
        # verbatim so that the rewrite does not delete any text.
        kept_sources[item["index"]] = strip_choice_prefix(sources[item["index"]])

    duplicates = sorted({value for value in source_normalized if source_normalized.count(value) > 1})
    if not sources:
        duplicates = sorted({value for value in normalized if normalized.count(value) > 1})
    if duplicates:
        raise QuizDataError(f"{path.name}: duplicate choices: {duplicates}")

    return QuizTask(
        path=path,
        data=data,
        task_id=task_id,
        question=question,
        choices=normalized,
        old_correct=correct,
        old_correct_idx=correct_idx,
        sources=kept_sources,
        truncated=truncated,
        authored=source_normalized,
    )


def load_tasks(data_dir: Path) -> Tuple[List[QuizTask], List[str]]:
    """Load every ``*.yaml`` file in ``data_dir``, sorted by quiz id.

    Files that violate the data contract are rejected (never modified) and
    reported; the remaining files are still processed.
    """
    tasks: List[QuizTask] = []
    problems: List[str] = []
    for path in sorted(data_dir.glob("*.yaml")):
        try:
            tasks.append(load_task(path))
        except QuizDataError as exc:
            problems.append(str(exc))
    tasks.sort(key=lambda task: task.task_id)
    return tasks, problems


# ---------------------------------------------------------------------------
# Balancing
# ---------------------------------------------------------------------------


def _balanced_quotas(tasks: Sequence[QuizTask]) -> List[int]:
    """How often each option position should carry the gold answer.

    The counts are as equal as possible across the available positions.  With
    145 six-choice questions this yields ``[25, 24, 24, 24, 24, 24]``.  A
    position is never used more often than the number of questions that can
    actually reach it (a question with four choices never uses position 4).
    """
    if not tasks:
        return []

    max_choices = max(task.n_choices for task in tasks)
    eligible = [sum(1 for task in tasks if task.n_choices > position) for position in range(max_choices)]

    quotas = [0] * max_choices
    remaining = len(tasks)
    # Positions with fewer eligible questions are served first so that the
    # scarce positions are not starved by the wide ones.
    order = sorted(range(max_choices), key=lambda position: (eligible[position], position))
    while remaining > 0:
        allocated = False
        for position in order:
            if remaining == 0:
                break
            if quotas[position] < eligible[position]:
                quotas[position] += 1
                remaining -= 1
                allocated = True
        if not allocated:  # pragma: no cover - defensive, the loop is feasible
            break
    return quotas


def build_targets(tasks: Sequence[QuizTask], seed: str) -> Dict[str, int]:
    """Map every quiz id to the position its gold answer should occupy.

    The balanced target list is shuffled with ``random.Random(seed)`` and then
    handed out to the questions in id order, which keeps the result
    reproducible without making the gold letter predictable per question.
    """
    quotas = _balanced_quotas(tasks)
    pool: List[int] = []
    for position, count in enumerate(quotas):
        pool.extend([position] * count)

    random.Random(seed).shuffle(pool)

    usage = [0] * len(quotas)
    targets: Dict[str, int] = {}
    for task in tasks:
        chosen = None
        for index, position in enumerate(pool):
            if position < task.n_choices:
                chosen = index
                break
        if chosen is None:
            # Every remaining target is out of range for this question: fall
            # back to the least used reachable position.
            position = min(range(task.n_choices), key=lambda candidate: (usage[candidate], candidate))
        else:
            position = pool.pop(chosen)
        usage[position] += 1
        targets[task.task_id] = position
    return targets


def rebalance_task(task: QuizTask, target: int, seed: str) -> None:
    """Place the gold option at ``target`` and shuffle the rest."""
    pairs = [
        (choice, source)
        for index, (choice, source) in enumerate(zip(task.choices, task.sources))
        if index != task.old_correct_idx
    ]
    # The canonical (sorted) input makes a second run with the same seed a
    # no-op: the result depends on the option multiset only, not on the order
    # the options happen to be stored in.  The preserved source line breaks
    # ties between options with the same parsed text.
    pairs.sort(key=lambda pair: (pair[0], pair[1] or ""))
    random.Random(f"{seed}:{task.task_id}").shuffle(pairs)

    new_choices = [choice for choice, _ in pairs]
    new_sources = [source for _, source in pairs]
    new_choices.insert(target, task.choices[task.old_correct_idx])
    new_sources.insert(target, task.sources[task.old_correct_idx])

    if sorted(new_choices) != sorted(task.choices):
        raise QuizDataError(f"{task.path.name}: rebalancing would change the option texts")

    task.new_choices = new_choices
    task.new_sources = new_sources
    task.new_correct = GOLD_LETTERS[target]


# ---------------------------------------------------------------------------
# Rendering and writing
# ---------------------------------------------------------------------------


def splice_source(text: str, token: str, source: str) -> str:
    """Replace ``token`` with the verbatim source lines of an option.

    A preserved source may span several lines.  Its continuation lines are
    re-indented to the column the scalar starts in, so that they stay part
    of the same YAML scalar instead of breaking out of the block sequence.
    """
    out: List[str] = []
    for line in text.splitlines(keepends=True):
        if token not in line:
            out.append(line)
            continue
        body = line.rstrip("\n")
        newline = line[len(body) :] or "\n"
        indent = len(body) - len(body.lstrip(" "))
        content_indent = " " * (indent + 2)
        source_lines = source.splitlines() or [source]
        out.append(body.replace(token, source_lines[0], 1) + newline)
        for extra in source_lines[1:]:
            out.append(content_indent + extra + newline)
    return "".join(out)


def render_task(task: QuizTask) -> str:
    """Render the migrated quiz file as YAML text.

    Options whose source text was truncated by a YAML comment are written
    back verbatim: the emitter would otherwise quote them, which would change
    the parsed option text and delete the tail from the file.
    """
    data = dict(task.data)
    data["choices"] = list(task.new_choices)
    data["correct_choice"] = task.new_correct
    data["dataset_version"] = DATASET_VERSION

    preserved = {
        index: source for index, source in enumerate(task.new_sources) if source is not None
    }
    if not preserved:
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)

    tokens: Dict[str, str] = {}
    choices: List[str] = []
    for index, choice in enumerate(task.new_choices):
        source = preserved.get(index)
        if source is None:
            choices.append(choice)
            continue
        token = f"__quizv2_source_{index}__"
        tokens[token] = source
        choices.append(token)
    data["choices"] = choices

    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    for token, source in tokens.items():
        text = splice_source(text, token, source)
    return text


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temporary file and an atomic rename."""
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(tasks: Sequence[QuizTask], seed: str, data_dir: Path) -> Dict[str, Any]:
    """Build the manifest describing the migration."""
    before: Dict[str, int] = {}
    after: Dict[str, int] = {}
    questions: List[Dict[str, Any]] = []
    truncated: List[Dict[str, Any]] = []
    for task in tasks:
        before[task.old_correct] = before.get(task.old_correct, 0) + 1
        after[task.new_correct] = after.get(task.new_correct, 0) + 1
        questions.append(
            {
                "id": task.task_id,
                "old_correct": task.old_correct,
                "new_correct": task.new_correct,
                "old_multiset_sha256": multiset_sha256(task.choices),
                "new_multiset_sha256": multiset_sha256(task.new_choices),
                "authored_text_sha256": task.authored_sha256,
            }
        )
        for item in task.truncated:
            truncated.append({"id": task.task_id, "file": task.path.name, **item})
    return {
        "dataset_version": DATASET_VERSION,
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "question_count": len(tasks),
        "before_distribution": {letter: before.get(letter, 0) for letter in GOLD_LETTERS},
        "after_distribution": {letter: after.get(letter, 0) for letter in GOLD_LETTERS},
        "truncated_option_count": len(truncated),
        "truncated_options": truncated,
        "content_preservation_valid": not truncated,
        "content_preservation_note": (
            ""
            if not truncated
            else (
                f"{len(truncated)} option(s) are truncated by a YAML comment: the multiset hashes"
                " cover the parsed text, not the authored text, so this manifest does not prove"
                " that the option text was preserved."
            )
        ),
        "questions": questions,
    }


def write_manifest(manifest: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _format_distribution(distribution: Dict[str, int]) -> str:
    return " ".join(f"{letter}={distribution.get(letter, 0)}" for letter in GOLD_LETTERS)


def _diff_for(task: QuizTask, new_text: str) -> str:
    original = task.path.read_text(encoding="utf-8")
    diff = difflib.unified_diff(
        original.splitlines(),
        new_text.splitlines(),
        fromfile=f"{task.path.name} (before)",
        tofile=f"{task.path.name} (after)",
        lineterm="",
    )
    return "\n".join(diff)


def print_report(
    tasks: Sequence[QuizTask],
    manifest: Dict[str, Any],
    changed: Sequence[QuizTask],
    rendered: Dict[str, str],
    problems: Sequence[str],
    mode: str,
) -> None:
    """Print the before/after report and a short unified-diff summary."""
    print("Quiz v2 rebalance")
    print("=" * 60)
    print(f"mode                : {mode}")
    print(f"data dir            : {manifest['data_dir']}")
    print(f"seed                : {manifest['seed']}")
    print(f"questions           : {manifest['question_count']}")
    print(f"before distribution : {_format_distribution(manifest['before_distribution'])}")
    print(f"after distribution  : {_format_distribution(manifest['after_distribution'])}")
    print(f"files that change   : {len(changed)}")

    preserved = sum(
        1
        for entry in manifest["questions"]
        if entry["old_multiset_sha256"] == entry["new_multiset_sha256"]
    )
    print(f"multisets preserved : {preserved}/{len(tasks)}")

    if manifest["truncated_option_count"]:
        print(
            f"WARNING             : {manifest['truncated_option_count']} option(s) are truncated by a YAML"
            " comment in the source file"
        )
        for item in manifest["truncated_options"]:
            print(f"    {item['id']} [{item['index']}] parsed={item['parsed']!r}")
            print(f"        authored={item['authored']!r}")

    if problems:
        print(f"rejected files      : {len(problems)}")
        for problem in problems:
            print(f"    {problem}")

    if changed:
        print()
        print(f"unified diff (first {min(DIFF_EXAMPLES, len(changed))} of {len(changed)} files)")
        for task in changed[:DIFF_EXAMPLES]:
            print()
            print(_diff_for(task, rendered[task.task_id]))


def print_truncation_refusal(manifest: Dict[str, Any]) -> None:
    """Explain why the migration refuses to write the dataset."""
    entries = manifest["truncated_options"]
    print(
        f"error: refusing to write: {len(entries)} option(s) are truncated by a YAML comment.",
        file=sys.stderr,
    )
    print(
        "The rewrite would drop the text behind the '#', so the migration could not claim that"
        " the option text was preserved.",
        file=sys.stderr,
    )
    print(
        "Fix: quote the authored line in the source file (without changing a single character)"
        " and run the migration again.",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    for entry in entries:
        source = entry["source"].splitlines()[0]
        print(f"  {entry['file']} [{entry['index']}] parsed={entry['parsed']!r}", file=sys.stderr)
        print(f"      authored line: {source}", file=sys.stderr)
        print(f'      fix: quote the whole option, e.g. - "{source}"', file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Use --allow-truncated to write anyway (test escape hatch only; the manifest then does"
        " not prove content preservation).",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify(data_dir: Path, manifest_path: Path, allow_truncated: bool = False) -> int:
    """Check the current dataset against a manifest."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not read manifest {manifest_path}: {exc}", file=sys.stderr)
        return 1

    expected = {entry["id"]: entry for entry in manifest.get("questions", [])}
    problems: List[str] = []
    checked = 0

    files = sorted(data_dir.glob("*.yaml"))
    if not files:
        print(f"error: no quiz files in {data_dir}", file=sys.stderr)
        return 1

    manifest_truncated = int(manifest.get("truncated_option_count") or 0)
    if manifest_truncated and not allow_truncated:
        problems.append(
            f"manifest records {manifest_truncated} truncated option(s): it does not prove that the"
            " option text was preserved (re-run with --allow-truncated to accept it anyway)"
        )

    seen = set()
    for path in files:
        try:
            task = load_task(path)
        except QuizDataError as exc:
            problems.append(str(exc))
            continue
        entry = expected.get(task.task_id)
        if entry is None:
            problems.append(f"{path.name}: quiz {task.task_id} is not in the manifest")
            continue
        seen.add(task.task_id)
        checked += 1

        digest = multiset_sha256(task.choices)
        if digest != entry["new_multiset_sha256"]:
            problems.append(
                f"{path.name}: option multiset hash {digest[:12]} != manifest {entry['new_multiset_sha256'][:12]}"
            )
        current_correct = str(task.data.get("correct_choice") or "").strip().upper()
        if current_correct != entry["new_correct"]:
            problems.append(f"{path.name}: correct_choice {current_correct} != manifest {entry['new_correct']}")

        expected_authored = entry.get("authored_text_sha256")
        if expected_authored and task.authored_sha256 and task.authored_sha256 != expected_authored:
            problems.append(
                f"{path.name}: authored text hash {task.authored_sha256[:12]} != manifest {expected_authored[:12]}"
            )

        if task.truncated and not allow_truncated:
            for item in task.truncated:
                problems.append(
                    f"{path.name}: option {item['index']} is truncated by a YAML comment"
                    f" (parsed={item['parsed']!r})"
                )

    for task_id in sorted(set(expected) - seen):
        problems.append(f"quiz {task_id} from the manifest is missing in {data_dir}")

    print("Quiz v2 verification")
    print("=" * 60)
    print(f"data dir   : {data_dir}")
    print(f"manifest   : {manifest_path}")
    print(f"checked    : {checked}/{len(expected)}")
    if problems:
        print(f"problems   : {len(problems)}")
        for problem in problems:
            print(f"    {problem}")
        return 1
    print("result     : OK - every option multiset and gold letter matches the manifest")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize and rebalance the quiz dataset (quiz-v2).")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory holding the quiz YAML files",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing")
    parser.add_argument("--write", action="store_true", help="Write the migrated files")
    parser.add_argument("--seed", default=DEFAULT_SEED, help=f"Seed for the shuffle (default: {DEFAULT_SEED})")
    parser.add_argument("--manifest", type=Path, default=None, help="Path of the JSON manifest to write")
    parser.add_argument("--verify", type=Path, default=None, help="Verify the dataset against an existing manifest")
    parser.add_argument(
        "--allow-truncated",
        action="store_true",
        help="Write/verify even though some options are truncated by a YAML comment (test escape hatch)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.data_dir.is_dir():
        print(f"error: data dir does not exist: {args.data_dir}", file=sys.stderr)
        return 1

    if args.verify is not None:
        return verify(args.data_dir, args.verify, allow_truncated=args.allow_truncated)

    tasks, problems = load_tasks(args.data_dir)
    if not tasks:
        print(f"error: no usable quiz files in {args.data_dir}", file=sys.stderr)
        for problem in problems:
            print(f"    {problem}", file=sys.stderr)
        return 1

    write = args.write and not args.dry_run
    mode = "write" if write else "dry-run"
    if args.write and args.dry_run:
        print("note: --dry-run wins over --write, nothing is written", file=sys.stderr)

    # The truncation check runs before anything is computed or written: a
    # truncated option would lose the text behind the '#' when the file is
    # rewritten, so the migration must not touch the dataset.
    if write:
        truncated = [
            {"id": task.task_id, "file": task.path.name, **item}
            for task in tasks
            for item in task.truncated
        ]
        if truncated and not args.allow_truncated:
            print_truncation_refusal({"truncated_options": truncated})
            return 1

    targets = build_targets(tasks, args.seed)
    for task in tasks:
        rebalance_task(task, targets[task.task_id], args.seed)

    manifest = build_manifest(tasks, args.seed, args.data_dir)

    rendered: Dict[str, str] = {}
    changed: List[QuizTask] = []
    for task in tasks:
        text = render_task(task)
        rendered[task.task_id] = text
        if text != task.path.read_text(encoding="utf-8"):
            changed.append(task)

    print_report(tasks, manifest, changed, rendered, problems, mode)

    if write:
        for task in tasks:
            atomic_write(task.path, rendered[task.task_id])
        print()
        print(f"wrote {len(changed)} file(s) in {args.data_dir}")

    if args.manifest is not None:
        write_manifest(manifest, args.manifest)
        print(f"wrote manifest to {args.manifest}")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
