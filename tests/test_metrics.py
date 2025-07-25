"""
Tests for NicheBench metrics.
"""

import pytest

from nichebench.metrics import METRIC_REGISTRY, checklist_accuracy


def test_metric_registry() -> None:
    """Test that metrics are properly registered."""
    assert "checklist_accuracy" in METRIC_REGISTRY
    assert METRIC_REGISTRY["checklist_accuracy"] == checklist_accuracy


def test_checklist_accuracy_computation() -> None:
    """Test checklist accuracy metric computation."""
    from nichebench.metrics.checklist import checklist_accuracy_fn

    # Test with simple checklist
    predictions = ["This code uses hooks and follows coding standards"]

    # Mock formatted_doc with checklist
    class MockDoc:
        def __init__(self, checklist: list) -> None:
            self.checklist = checklist

    doc = MockDoc(["hooks", "coding standards", "dependency injection"])

    result = checklist_accuracy_fn(predictions, doc)

    # Should get 2/3 = 0.67 (approximately)
    assert 0.6 <= result <= 0.7


def test_checklist_accuracy_empty() -> None:
    """Test checklist accuracy with empty inputs."""
    from nichebench.metrics.checklist import checklist_accuracy_fn

    class MockDoc:
        def __init__(self, checklist: list) -> None:
            self.checklist = checklist

    doc = MockDoc([])
    result = checklist_accuracy_fn([], doc)
    assert result == 0.0
