"""YAML loader for test cases under framework packs."""
from __future__ import annotations
import yaml
from pathlib import Path
from typing import List

from nichebench.core.datamodel import TestCaseSpec, TaskSpec


def load_testcase_from_file(path: Path) -> TestCaseSpec:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    tc = TestCaseSpec(
        id=str(data.get("id") or path.stem),
        type=("quiz" if "question" in data else "code" if "prompt" in data else "bug"),
        raw=data,
        context=data.get("context"),
        summary=data.get("summary"),
        prompt=data.get("prompt"),
        choices=data.get("choices"),
        correct_choice=data.get("correct_choice"),
        checklist=data.get("checklist") or data.get("judge_checklist") or data.get("solution_includes"),
    )
    return tc


def load_taskspecs_for_framework(framework_path: Path, framework_name: str) -> List[TaskSpec]:
    data_dir = framework_path / "data"
    tasks: List[TaskSpec] = []
    if not data_dir.exists():
        return tasks

    for task_type_dir in data_dir.iterdir():
        if not task_type_dir.is_dir():
            continue
        ts = TaskSpec(framework=framework_name, task_type=task_type_dir.name, file_path=str(task_type_dir))
        for yaml_file in sorted(task_type_dir.glob("*.yaml")):
            try:
                tc = load_testcase_from_file(yaml_file)
                ts.testcases.append(tc)
            except Exception as e:
                # skip bad files but continue
                print(f"warning: failed to load {yaml_file}: {e}")
        tasks.append(ts)
    return tasks
