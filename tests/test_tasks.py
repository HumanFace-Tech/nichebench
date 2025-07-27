"""
Tests for NicheBench tasks.
"""

import pytest

from nichebench.tasks import TASK_REGISTRY, get_available_tasks


def test_task_registry() -> None:
    """Test that tasks are properly registered."""
    available_tasks = get_available_tasks()
    # Check that we have some tasks registered
    assert len(available_tasks) > 0
    # Test with the actual task names from our configurations
    assert any("drupal" in task.lower() for task in available_tasks)
    assert any("wordpress" in task.lower() for task in available_tasks)


def test_drupal_task_instantiation() -> None:
    """Test Drupal task configurations exist."""
    # Test that drupal tasks are in registry
    task_names = [t.name for t in TASK_REGISTRY if hasattr(t, "name")]
    drupal_tasks = [t for t in task_names if "drupal" in t.lower()]
    assert len(drupal_tasks) > 0


def test_wordpress_task_instantiation() -> None:
    """Test WordPress task configurations exist."""
    # Test that wordpress tasks are in registry
    task_names = [t.name for t in TASK_REGISTRY if hasattr(t, "name")]
    wordpress_tasks = [t for t in task_names if "wordpress" in t.lower()]
    assert len(wordpress_tasks) > 0


def test_task_prompt_generation() -> None:
    """Test task prompt generation."""
    # Test that we can import prompt functions
    from nichebench.tasks.drupal.prompt_functions import drupal_quiz_prompt

    test_line = {
        "context": "Drupal 10 module development",
        "question": "How do you create a custom block?",
        "choices": ["A) Use hook_block_info", "B) Extend BlockBase", "C) Use form API"],
    }

    doc = drupal_quiz_prompt(test_line, "test_task")
    assert hasattr(doc, "query")
    assert "custom block" in doc.query
