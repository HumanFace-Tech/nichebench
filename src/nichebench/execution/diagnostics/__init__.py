"""Diagnostics package — runtime trace and post-hoc forensics.

This package merges what was previously split across ``core/runtime_diagnostics``
(in-flight stage tracing + failure classification) and ``core/forensics``
(post-hoc artifact analysis of trial directories).

Module layout
-------------
trace    — RuntimeTrace, RuntimeFailure, classify_runtime_failure, first_failed_stage
forensics — collect_reports, _analyze_trial_dir, _analyze_trajectory, path helpers
report   — format_text_report

Public API is re-exported from this package's __init__ so callers can use
``from nichebench.execution.diagnostics import collect_reports``.
"""

from nichebench.execution.diagnostics.forensics import collect_reports
from nichebench.execution.diagnostics.report import format_text_report
from nichebench.execution.diagnostics.trace import (
    RUNTIME_STAGES,
    RuntimeFailure,
    RuntimeTrace,
    classify_runtime_failure,
    first_failed_stage,
)

__all__ = [
    # trace
    "RUNTIME_STAGES",
    "RuntimeFailure",
    "RuntimeTrace",
    "classify_runtime_failure",
    "first_failed_stage",
    # forensics
    "collect_reports",
    "format_text_report",
]
