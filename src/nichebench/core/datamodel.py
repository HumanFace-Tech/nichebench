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
    # Runtime task fields
    source: Optional[Dict[str, Any]] = None
    environment: Optional[Dict[str, Any]] = None
    agent: Optional[Dict[str, Any]] = None
    checks: Optional[List[Dict[str, Any]]] = None
    scoring: Optional[Dict[str, Any]] = None
    deliverables: Optional[List[str]] = None
    base_branch: Optional[str] = None
    resolved_sha: Optional[str] = None
    file_path: Optional[str] = None
    browser_artifacts: Optional[Dict[str, Any]] = None


@dataclass
class TaskSpec:
    framework: str
    task_type: str
    file_path: str
    testcases: List[TestCaseSpec] = field(default_factory=list)
