"""No live DeepEval references anywhere in the package.

Guards for the code-generation v2 C1 slice (DeepEval removal):

* ``src/`` and ``pyproject.toml`` must contain **zero** occurrences of
  ``deepeval`` / ``DeepEval`` / ``LLMTestCase`` — live source and the
  dependency manifest are unconditionally clean.
* ``tests/`` may only contain *negative* references (tests that assert
  DeepEval is absent); any actual usage — an import, a ``DeepEval*``
  symbol, or ``LLMTestCase`` — fails.
* ``nichebench.cli.app`` must import with ``deepeval`` blocked at
  ``sys.meta_path`` and no ``deepeval`` module loaded afterwards.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Callable, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]

STRICT_TOKEN = re.compile(r"deepeval|DeepEval|LLMTestCase")

# Usage patterns allowed to appear only as negative assertions in tests.
# Guard strings like ``"deepeval import blocked by test"`` or
# ``assert "deepeval" not in source`` intentionally do not match.
USAGE_PATTERNS = (
    re.compile(r"\bfrom\s+deepeval\b"),
    re.compile(r"\bimport\s+deepeval\b"),
    re.compile(r"DeepEval[A-Za-z_]"),
    re.compile(r"\bLLMTestCase\b"),
)

# Modules deleted by the v2 C1 slice; none of them may resolve.
DELETED_MODULES = (
    "nichebench.metrics.code_generation_metric",
    "nichebench.metrics.bug_fixing_metric",
)

_SCAN_SUFFIXES = (".py", ".toml", ".md", ".txt", ".yml", ".yaml", ".cfg", ".ini")


def _iter_text_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        if path.suffix not in _SCAN_SUFFIXES:
            continue
        files.append(path)
    return files


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data:
        raise UnicodeDecodeError("binary", data, 0, 1, "binary file")
    return data.decode("utf-8")


def _scan(paths: List[Path], matcher: Callable[[str], bool]) -> List[Tuple[str, int, str]]:
    hits: List[Tuple[str, int, str]] = []
    for path in paths:
        try:
            text = _read_text(path)
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if matcher(line):
                hits.append((str(path.relative_to(REPO_ROOT)), lineno, line.strip()))
    return hits


def _strict_matcher(line: str) -> bool:
    return bool(STRICT_TOKEN.search(line))


def _usage_matcher(line: str) -> bool:
    return any(pattern.search(line) for pattern in USAGE_PATTERNS)


def test_live_source_has_no_deepeval_reference() -> None:
    """``src/`` and ``pyproject.toml`` must be free of any DeepEval token."""
    paths = _iter_text_files(REPO_ROOT / "src") + [REPO_ROOT / "pyproject.toml"]
    hits = _scan(paths, _strict_matcher)
    assert hits == [], f"live source must not reference deepeval: {hits}"


def test_tests_have_no_deepeval_usage() -> None:
    """Tests may assert DeepEval's absence but never actually use it."""
    here = Path(__file__).resolve()
    paths = [path for path in _iter_text_files(REPO_ROOT / "tests") if path.resolve() != here]
    hits = _scan(paths, _usage_matcher)
    assert hits == [], f"tests must not use deepeval/LLMTestCase: {hits}"


def test_deleted_metric_modules_do_not_resolve() -> None:
    for module_name in DELETED_MODULES:
        assert importlib.util.find_spec(module_name) is None, module_name


class _BlockDeepevalFinder:
    """``sys.meta_path`` finder that refuses to import ``deepeval``."""

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname == "deepeval" or fullname.startswith("deepeval."):
            raise ImportError(f"deepeval import blocked by test: {fullname}")


def test_cli_app_imports_without_deepeval() -> None:
    """The CLI entrypoint imports cleanly with ``deepeval`` blocked."""
    sys.modules.pop("nichebench.cli.app", None)

    finder = _BlockDeepevalFinder()
    sys.meta_path.insert(0, finder)
    try:
        module = importlib.import_module("nichebench.cli.app")
    finally:
        sys.meta_path.remove(finder)

    assert module is not None
    loaded = [name for name in sys.modules if name == "deepeval" or name.startswith("deepeval.")]
    assert loaded == []
