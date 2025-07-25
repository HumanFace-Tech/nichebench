"""
NicheBench tasks module.

This module contains task definitions for framework-specific benchmarks
following LightEval conventions.
"""

from typing import Dict, List, Optional

from lighteval.tasks.lighteval_task import LightevalTaskConfig

from .drupal import TASKS_TABLE as DRUPAL_TASKS
from .wordpress import TASKS_TABLE as WORDPRESS_TASKS

# Aggregate all tasks for LightEval discovery
TASKS_TABLE = [
    *DRUPAL_TASKS,
    *WORDPRESS_TASKS,
]

# Task registry for CLI discovery
TASK_REGISTRY = {}
for task in TASKS_TABLE:
    TASK_REGISTRY[task.name] = task


def get_available_tasks() -> List[str]:
    """Get list of available task names."""
    return list(TASK_REGISTRY.keys())


def get_tasks_by_framework(framework: str) -> List[str]:
    """Get tasks filtered by framework."""
    framework_map = {
        "drupal": [task.name for task in DRUPAL_TASKS],
        "wordpress": [task.name for task in WORDPRESS_TASKS],
    }
    return framework_map.get(framework.lower(), [])


def get_tasks_by_category(framework: str, category: str) -> List[str]:
    """Get tasks filtered by framework and category."""
    all_framework_tasks = get_tasks_by_framework(framework)

    if category == "all":
        return all_framework_tasks
    elif category == "quiz":
        return [task for task in all_framework_tasks if "quiz" in task]
    elif category in ["code", "codegen", "generation"]:
        return [
            task
            for task in all_framework_tasks
            if any(term in task for term in ["code", "generation"])
        ]
    else:
        return []


def get_task_config(task_name: str) -> Optional[LightevalTaskConfig]:
    """Get task configuration by name."""
    return TASK_REGISTRY.get(task_name)


__all__ = [
    "TASKS_TABLE",
    "TASK_REGISTRY",
    "get_available_tasks",
    "get_tasks_by_framework",
    "get_tasks_by_category",
    "get_task_config",
]
