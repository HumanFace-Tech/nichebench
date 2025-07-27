"""Task definitions and registrations for NicheBench."""

import importlib
import os
from typing import Dict, List, Optional

from lighteval.tasks.lighteval_task import LightevalTaskConfig


def _discover_frameworks() -> List[str]:
    """
    Auto-discover available frameworks by scanning the tasks directory.

    Returns:
        List of framework names (directory names in tasks/)
    """
    tasks_dir = os.path.dirname(__file__)
    frameworks = []

    for item in os.listdir(tasks_dir):
        item_path = os.path.join(tasks_dir, item)
        # Check if it's a directory and not __pycache__ or similar
        if (
            os.path.isdir(item_path)
            and not item.startswith("__")
            and not item.startswith(".")
        ):
            frameworks.append(item)

    return sorted(frameworks)


def _import_framework_tasks() -> Dict[str, List[LightevalTaskConfig]]:
    """
    Dynamically import task configurations from all discovered frameworks.

    Returns:
        Dict mapping framework names to their task configurations
    """
    framework_tasks = {}
    frameworks = _discover_frameworks()

    for framework in frameworks:
        try:
            # Import the framework module
            module = importlib.import_module(f".{framework}", package=__name__)

            # Look for TASKS_TABLE attribute
            if hasattr(module, "TASKS_TABLE"):
                framework_tasks[framework] = module.TASKS_TABLE
            else:
                print(f"Warning: Framework '{framework}' has no TASKS_TABLE attribute")
                framework_tasks[framework] = []

        except ImportError as e:
            print(f"Warning: Could not import framework '{framework}': {e}")
            framework_tasks[framework] = []

    return framework_tasks


# Auto-discover and import all frameworks
_FRAMEWORK_TASKS = _import_framework_tasks()

# Global task registry for LightEval - flatten all framework tasks
TASK_REGISTRY: List[LightevalTaskConfig] = []
for framework_task_list in _FRAMEWORK_TASKS.values():
    TASK_REGISTRY.extend(framework_task_list)

# For backward compatibility - create TASKS_TABLE as alias
TASKS_TABLE = TASK_REGISTRY

# For backward compatibility - create dict-style task registry
TASK_REGISTRY_DICT = {}
for task in TASK_REGISTRY:
    TASK_REGISTRY_DICT[task.name] = task


def get_available_tasks() -> List[str]:
    """Get list of available task names."""
    return [task.name for task in TASK_REGISTRY]


def get_available_frameworks() -> List[str]:
    """
    Get list of all available frameworks.

    Returns:
        List of framework names
    """
    return list(_FRAMEWORK_TASKS.keys())


def get_tasks_by_framework(framework_name: str) -> List[str]:
    """
    Get all task names for a specific framework.

    Args:
        framework_name: Name of the framework ("drupal", etc.)

    Returns:
        List of task names for the framework
    """
    if framework_name not in _FRAMEWORK_TASKS:
        return []

    return [task.name for task in _FRAMEWORK_TASKS[framework_name]]


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
    elif category in ["bug", "fix", "fixing"]:
        return [
            task
            for task in all_framework_tasks
            if any(term in task for term in ["bug", "fix"])
        ]
    else:
        return []


def get_task_categories(framework: str) -> List[str]:
    """
    Get available categories for a framework.

    Args:
        framework: Name of the framework

    Returns:
        List of available categories
    """
    tasks = get_tasks_by_framework(framework)
    categories = set(["all"])  # Always include "all"

    for task in tasks:
        task_lower = task.lower()
        if "quiz" in task_lower:
            categories.add("quiz")
        if any(term in task_lower for term in ["code", "generation"]):
            categories.add("code")
        if any(term in task_lower for term in ["bug", "fix"]):
            categories.add("bug")

    return sorted(list(categories))


def get_task_config(task_name: str) -> Optional[LightevalTaskConfig]:
    """Get task configuration by name."""
    return TASK_REGISTRY_DICT.get(task_name)


__all__ = [
    "TASKS_TABLE",
    "TASK_REGISTRY",
    "get_available_tasks",
    "get_available_frameworks",
    "get_tasks_by_framework",
    "get_tasks_by_category",
    "get_task_categories",
    "get_task_config",
]
