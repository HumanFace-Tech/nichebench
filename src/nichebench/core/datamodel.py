"""Core datamodels: TaskSpec and TestCaseSpec."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TestCaseSpec:
    id: str
    type: str
    raw: Dict[str, Any]
    context: Optional[str] = None
    summary: Optional[str] = None
    prompt: Optional[str] = None
    choices: Optional[List[str]] = None
    correct_choice: Optional[str] = None
    checklist: Optional[List[str]] = None


@dataclass
class TaskSpec:
    framework: str
    task_type: str
    file_path: str
    testcases: List[TestCaseSpec] = field(default_factory=list)
