"""Unit tests for scripts/quiz_v2_repair_choices.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

REPAIR_PATH = Path(__file__).resolve().parents[3] / "scripts" / "quiz_v2_repair_choices.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("quiz_v2_repair_choices", REPAIR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["quiz_v2_repair_choices"] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("quiz_v2_repair_choices", None)


def _fixture(tmp_path: Path) -> Path:
    path = tmp_path / "quiz_001.yaml"
    path.write_text(
        """id: q1
context: test
summary: test
question: Which option?
choices:
  - A) Plain option.
  - B) Set a non-zero #cache['max-age'] and keep POST safe.
  - C) Put `module_name.hooks_converted: true`.
correct_choice: B
""",
        encoding="utf-8",
    )
    return path


def test_dry_run_does_not_touch_the_file(tmp_path: Path) -> None:
    module = _load_module()
    path = _fixture(tmp_path)
    before = path.read_text(encoding="utf-8")

    truncated, mappings = module.repair_file(path, write=False)

    assert (truncated, mappings) == (1, 1)
    assert path.read_text(encoding="utf-8") == before


def test_write_repairs_truncation_and_mapping(tmp_path: Path) -> None:
    module = _load_module()
    path = _fixture(tmp_path)

    truncated, mappings = module.repair_file(path, write=True)
    assert (truncated, mappings) == (1, 1)

    choices = yaml.safe_load(path.read_text(encoding="utf-8"))["choices"]
    assert all(isinstance(choice, str) for choice in choices)
    assert "#cache['max-age']" in choices[1]
    assert "module_name.hooks_converted: true" in choices[2]

    # Idempotent: nothing left to repair.
    assert module.repair_file(path, write=True) == (0, 0)
