"""Unit tests for the quiz-v2 defensive loader normalisation.

Covers:

* choice text normalisation (letter prefixes, mapping choices, plain text);
* ``raw`` is never mutated by the loader;
* ``dataset_version`` defaults and overrides;
* ``TestResult.to_dict()`` carries ``dataset_version``.
"""

from pathlib import Path
from typing import Any, Dict, List

import pytest

from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.framework_loader import load_testcase_from_file
from nichebench.execution.result import TestResult


def _write_yaml(tmp_path: Path, data: str, name: str = "task.yaml") -> Path:
    path = tmp_path / name
    path.write_text(data, encoding="utf-8")
    return path


def _write_typed_yaml(tmp_path: Path, subdir: str, data: str, name: str = "task.yaml") -> Path:
    """Write a task YAML into a task-type subdirectory (e.g. ``bug_fixing/``)."""
    directory = tmp_path / subdir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(data, encoding="utf-8")
    return path


def _quiz_yaml(choices_block: str, extra: str = "") -> str:
    return (
        "id: quiz_v2_001\n"
        "question: Which option is correct?\n"
        "summary: A quiz summary.\n"
        "context: Some Drupal context.\n"
        "choices:\n" + choices_block + "correct_choice: B\n" + extra
    )


def _load_choices(tmp_path: Path, choices_block: str, extra: str = "") -> List[str]:
    path = _write_yaml(tmp_path, _quiz_yaml(choices_block, extra))
    spec = load_testcase_from_file(path)
    assert spec.choices is not None
    return spec.choices


def test_strips_single_letter_prefix_forms(tmp_path: Path) -> None:
    """``A) text``, ``(A) text``, ``A. text``, ``A: text`` and ``A - text`` are de-prefixed."""
    choices = _load_choices(
        tmp_path,
        "  - A) First option\n"
        "  - (A) Second option\n"
        "  - A. Third option\n"
        "  - A: Fourth option\n"
        "  - A - Fifth option\n",
    )

    assert choices == [
        "First option",
        "Second option",
        "Third option",
        "Fourth option",
        "Fifth option",
    ]


def test_strips_repeated_letter_prefixes(tmp_path: Path) -> None:
    """A doubled prefix is removed completely (``A) A) text`` → ``text``)."""
    choices = _load_choices(
        tmp_path,
        "  - A) A) Switch CI to GitHub Actions\n"
        "  - B) B) Keep the legacy pipeline\n"
        "  - C) C) Do nothing\n",
    )

    assert choices == [
        "Switch CI to GitHub Actions",
        "Keep the legacy pipeline",
        "Do nothing",
    ]


def test_plain_choices_are_unchanged(tmp_path: Path) -> None:
    """Plain option text is preserved verbatim."""
    choices = _load_choices(
        tmp_path,
        "  - Use the service container\n"
        "  - Inject the configuration factory\n"
        "  - Call the module handler\n",
    )

    assert choices == [
        "Use the service container",
        "Inject the configuration factory",
        "Call the module handler",
    ]


def test_mapping_choice_renders_as_key_value(tmp_path: Path) -> None:
    """A YAML mapping choice renders as ``key: value`` — never ``str(dict)``."""
    path = _write_yaml(
        tmp_path,
        "id: quiz_v2_map\n"
        "question: Which setting is required?\n"
        "choices:\n"
        "  - module_name.hooks_converted: true\n"
        "correct_choice: A\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.choices == ["module_name.hooks_converted: true"]
    assert spec.choices is not None
    assert "{" not in spec.choices[0]


def test_normalization_is_idempotent(tmp_path: Path) -> None:
    """Normalising already-normalised text is a no-op."""
    path = _write_yaml(
        tmp_path,
        _quiz_yaml("  - A) First option\n  - B) Second option\n"),
    )
    spec = load_testcase_from_file(path)

    assert spec.choices == ["First option", "Second option"]

    from nichebench.core.quiz_text import normalize_choice

    for choice in spec.choices or []:
        assert normalize_choice(choice) == choice


def test_raw_choices_are_not_mutated(tmp_path: Path) -> None:
    """The loader normalises ``choices`` but leaves ``raw`` untouched."""
    path = _write_yaml(
        tmp_path,
        "id: quiz_v2_raw\n"
        "question: Which option is correct?\n"
        "choices:\n"
        "  - A) First option\n"
        "  - module_name.hooks_converted: true\n"
        "correct_choice: A\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.choices == ["First option", "module_name.hooks_converted: true"]
    assert spec.raw["choices"][0] == "A) First option"
    assert spec.raw["choices"][1] == {"module_name.hooks_converted": True}


def test_quiz_dataset_version_defaults_to_quiz_v1(tmp_path: Path) -> None:
    """A quiz without an explicit version defaults to ``quiz-v1``."""
    path = _write_yaml(tmp_path, _quiz_yaml("  - A) First option\n  - B) Second option\n"))
    spec = load_testcase_from_file(path)

    assert spec.type == "quiz"
    assert spec.dataset_version == "quiz-v1"


def test_quiz_dataset_version_reads_explicit_value(tmp_path: Path) -> None:
    """An explicit ``dataset_version`` is carried through verbatim."""
    path = _write_yaml(
        tmp_path,
        _quiz_yaml("  - First option\n  - Second option\n", extra="dataset_version: quiz-v2\n"),
    )
    spec = load_testcase_from_file(path)

    assert spec.dataset_version == "quiz-v2"


def test_code_task_has_no_dataset_version(tmp_path: Path) -> None:
    """A code task does not get the quiz default."""
    path = _write_yaml(
        tmp_path,
        "id: code_001\n"
        "prompt: Write a service class.\n"
        "checklist:\n"
        "  - Implements the interface\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.type == "code"
    assert spec.dataset_version is None


def test_bug_task_has_no_dataset_version(tmp_path: Path) -> None:
    """A bug-fixing task does not get the quiz default."""
    path = _write_yaml(
        tmp_path,
        "id: bug_001\n"
        "task_description: Fix the broken plugin.\n"
        "checklist:\n"
        "  - The plugin loads\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.type == "bug"
    assert spec.dataset_version is None


def test_explicit_dataset_version_on_code_task(tmp_path: Path) -> None:
    """A non-quiz task may still declare a dataset version."""
    path = _write_yaml(
        tmp_path,
        "id: code_002\n"
        "prompt: Write a service class.\n"
        "dataset_version: code-v2\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.dataset_version == "code-v2"


def test_runtime_task_dataset_version_is_not_defaulted(tmp_path: Path) -> None:
    """Runtime manifests never fall back to the quiz default."""
    path = _write_yaml(
        tmp_path,
        "task_id: runtime_001\n"
        "task_type: runtime\n"
        "title: A runtime task\n"
        "description_structured:\n"
        "  background: Some background.\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.type == "runtime"
    assert spec.dataset_version is None


def _result(test_case: TestCaseSpec) -> TestResult:
    return TestResult(
        framework="drupal",
        category="quiz",
        test_case=test_case,
        mut_model="groq/model",
        judge_model="groq/judge",
    )


def test_result_to_dict_includes_dataset_version() -> None:
    """``TestResult.to_dict()`` carries the dataset version when present."""
    test_case = TestCaseSpec(
        id="quiz_v2_001",
        type="quiz",
        raw={"question": "Q?"},
        choices=["A", "B"],
        correct_choice="A",
        dataset_version="quiz-v2",
    )
    result = _result(test_case)

    payload: Dict[str, Any] = result.to_dict()

    assert payload["dataset_version"] == "quiz-v2"


def test_result_to_dict_omits_dataset_version_when_absent() -> None:
    """``dataset_version`` is absent when the spec does not carry one."""
    test_case = TestCaseSpec(
        id="code_001",
        type="code",
        raw={"prompt": "Write a service class."},
    )
    result = _result(test_case)

    payload: Dict[str, Any] = result.to_dict()

    assert "dataset_version" not in payload


# ─── Classic task-type detection order ───────────────────────────────────────
# Priority: explicit ``task_type`` > parent directory name > key sniffing.


def test_bug_yaml_with_prompt_in_bug_fixing_dir_is_bug(tmp_path: Path) -> None:
    """A bug task with a top-level ``prompt`` must not be classified as code."""
    path = _write_typed_yaml(
        tmp_path,
        "bug_fixing",
        "id: bug_dir_001\n"
        "prompt: Fix the broken plugin.\n"
        "checklist:\n"
        "  - The plugin loads\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.type == "bug"
    assert spec.prompt == "Fix the broken plugin."


def test_code_yaml_in_code_generation_dir_is_code(tmp_path: Path) -> None:
    """A code task under ``code_generation/`` classifies as code."""
    path = _write_typed_yaml(tmp_path, "code_generation", "id: code_dir_001\nprompt: Write a service class.\n")
    spec = load_testcase_from_file(path)

    assert spec.type == "code"


def test_quiz_yaml_in_quiz_dir_is_quiz(tmp_path: Path) -> None:
    """A quiz task under ``quiz/`` classifies as quiz."""
    path = _write_typed_yaml(
        tmp_path,
        "quiz",
        "id: quiz_dir_001\n"
        "question: Which option is correct?\n"
        "choices:\n"
        "  - First option\n"
        "  - Second option\n"
        "correct_choice: A\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.type == "quiz"


def test_directory_beats_key_sniffing(tmp_path: Path) -> None:
    """The parent directory name wins over top-level key sniffing."""
    path = _write_typed_yaml(tmp_path, "quiz", "id: quiz_dir_002\nprompt: Not a question key.\n")
    spec = load_testcase_from_file(path)

    assert spec.type == "quiz"


@pytest.mark.parametrize(
    "explicit,expected",
    [
        ("quiz", "quiz"),
        ("code_generation", "code"),
        ("code", "code"),
        ("bug_fixing", "bug"),
        ("bug", "bug"),
    ],
)
def test_explicit_task_type_wins_over_directory_and_keys(tmp_path: Path, explicit: str, expected: str) -> None:
    """An explicit ``task_type`` beats both the directory name and key sniffing."""
    path = _write_typed_yaml(
        tmp_path,
        "bug_fixing",
        f"id: explicit_001\ntask_type: {explicit}\nprompt: Something.\n",
    )
    spec = load_testcase_from_file(path)

    assert spec.type == expected
