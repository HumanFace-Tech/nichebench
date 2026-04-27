"""YAML loader for test cases under framework packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from nichebench.core.datamodel import TaskSpec, TestCaseSpec


def _stringify_list(values: Any) -> str:
    if not isinstance(values, list):
        return ""

    items = [str(value).strip() for value in values if str(value).strip()]
    return "\n".join(f"- {item}" for item in items)


def _compose_runtime_context(description_structured: Any) -> Optional[str]:
    if not isinstance(description_structured, dict):
        return None

    parts: List[str] = []

    technical_hints = _stringify_list(description_structured.get("technical_hints"))
    if technical_hints:
        parts.append(f"Technical hints:\n{technical_hints}")

    out_of_scope = _stringify_list(description_structured.get("out_of_scope"))
    if out_of_scope:
        parts.append(f"Out of scope:\n{out_of_scope}")

    context = "\n\n".join(parts).strip()
    return context or None


def _compose_runtime_prompt(data: Dict[str, Any]) -> Optional[str]:
    description_structured = data.get("description_structured")
    if not isinstance(description_structured, dict):
        return None

    parts: List[str] = []

    background = str(description_structured.get("background") or "").strip()
    if background:
        parts.append(background)

    acceptance_criteria = _stringify_list(description_structured.get("acceptance_criteria"))
    if acceptance_criteria:
        parts.append(f"Acceptance criteria:\n{acceptance_criteria}")

    prompt = "\n\n".join(parts).strip()
    return prompt or None


def load_testcase_from_file(path: Path) -> TestCaseSpec:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    if not isinstance(data, dict):
        raise ValueError(f"YAML file {path} did not parse to a mapping")

    if data.get("task_type") == "runtime" or (path.parent.name == "manifest" and path.parent.parent.name == "tasks"):
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        task_id = str(data.get("task_id") or data.get("id") or path.stem)
        prompt = data.get("prompt") or _compose_runtime_prompt(data)
        tc = TestCaseSpec(
            id=task_id,
            type="runtime",
            raw=data,
            context=_compose_runtime_context(data.get("description_structured")),
            summary=str(data.get("title") or data.get("summary") or "") or None,
            prompt=prompt,
            source=source or None,
            environment=data.get("environment") if isinstance(data.get("environment"), dict) else None,
            agent=data.get("agent") if isinstance(data.get("agent"), dict) else None,
            checks=data.get("checks"),
            scoring=data.get("scoring") if isinstance(data.get("scoring"), dict) else None,
            deliverables=data.get("deliverables"),
            base_branch=(source.get("base_branch") or source.get("task_branch")) if source else None,
            resolved_sha=source.get("resolved_sha") if source else None,
            browser_artifacts=(
                data.get("browser_artifacts") if isinstance(data.get("browser_artifacts"), dict) else None
            ),
            file_path=str(path),
        )
        return tc

    # Determine type
    if "question" in data:
        tc_type = "quiz"
    elif "prompt" in data:
        tc_type = "code"
    elif "task_type" in data and data["task_type"] == "runtime":
        tc_type = "runtime"
    else:
        tc_type = "bug"

    tc = TestCaseSpec(
        id=str(data.get("id") or path.stem),
        type=tc_type,
        raw=data,
        context=data.get("context"),
        summary=data.get("summary"),
        prompt=data.get("prompt") or data.get("task_description"),
        choices=data.get("choices"),
        correct_choice=data.get("correct_choice"),
        checklist=data.get("checklist") or data.get("judge_checklist") or data.get("solution_includes"),
        source=data.get("source"),
        environment=data.get("environment"),
        agent=data.get("agent"),
        checks=data.get("checks"),
        scoring=data.get("scoring"),
        deliverables=data.get("deliverables"),
        base_branch=data.get("source", {}).get("base_branch") if isinstance(data.get("source"), dict) else None,
        resolved_sha=data.get("source", {}).get("resolved_sha") if isinstance(data.get("source"), dict) else None,
        browser_artifacts=data.get("browser_artifacts") if isinstance(data.get("browser_artifacts"), dict) else None,
        file_path=str(path),
    )
    return tc


def load_taskspecs_for_framework(framework_path: Path, framework_name: str) -> List[TaskSpec]:
    data_dir = framework_path / "data"
    tasks: List[TaskSpec] = []
    if not data_dir.exists():
        return tasks

    runtime_manifest_dir = data_dir / "tasks" / "manifest"
    if runtime_manifest_dir.exists():
        ts = TaskSpec(framework=framework_name, task_type="runtime", file_path=str(runtime_manifest_dir))
        for yaml_file in sorted(runtime_manifest_dir.glob("*.yaml")):
            try:
                tc = load_testcase_from_file(yaml_file)
                ts.testcases.append(tc)
            except Exception as e:
                print(f"warning: failed to load {yaml_file}: {e}")
        tasks.append(ts)
        return tasks

    for task_type_dir in data_dir.iterdir():
        if not task_type_dir.is_dir():
            continue
        ts = TaskSpec(
            framework=framework_name,
            task_type=task_type_dir.name,
            file_path=str(task_type_dir),
        )
        for yaml_file in sorted(task_type_dir.glob("*.yaml")):
            try:
                tc = load_testcase_from_file(yaml_file)
                ts.testcases.append(tc)
            except Exception as e:
                # skip bad files but continue
                print(f"warning: failed to load {yaml_file}: {e}")
        tasks.append(ts)
    return tasks
