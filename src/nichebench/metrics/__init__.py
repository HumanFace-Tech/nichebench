"""
NicheBench metrics module.

This module contains custom metrics for evaluating framework-specific tasks.
"""

from .checklist import checklist_accuracy

# Registry for metric discovery
METRIC_REGISTRY = {
    "checklist_accuracy": checklist_accuracy,
}

__all__ = ["checklist_accuracy", "METRIC_REGISTRY"]
