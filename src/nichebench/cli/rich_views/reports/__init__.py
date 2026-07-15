"""Rich report rendering package for NicheBench run results.

This package provides Rich-powered console output for two distinct phases:

1. **Run completion** — after a ``nichebench run`` finishes,
   :func:`render_run_completion_report <completion.render_run_completion_report>`
   displays a summary table and, optionally, per-test failure details.
2. **Run listing / selection** — :func:`render_run_list <selectors.render_run_list>`
   shows available run directories;
   :func:`render_run_selector <selectors.render_run_selector>` adds an interactive
   paginated picker that returns the chosen run path.

Package layout
=============
``completion.py``
    Main entry point for run completion reports.
    Owns: ``render_run_completion_report``.
``detail_runtime.py``
    Per-test detail table for the ``runtime`` category.
    Owns: ``_render_runtime_details_table``.
``detail_quiz.py``
    Per-test detail table for the ``quiz`` category.
    Owns: ``_render_quiz_details_table``.
``detail_code.py``
    Per-test detail table for ``code_generation`` and ``bug_fixing`` categories.
    Owns: ``_render_code_details_table``.
``selectors.py``
    Interactive run selector and run listing.
    Owns: ``render_run_selector``, ``render_run_list``.

Import compatibility
====================
All public functions are re-exported at the package level so that existing
imports such as::

    from nichebench.cli.rich_views.reports import render_run_completion_report

continue to work without modification.

Rendering boundaries
===================
* All functions are side-effect-only (write to ``Console``); they do not
  return structured data destined for further processing.
* Category-specific detail tables (``_render_runtime_details_table`` etc.)
  are internal helpers — the public entry point makes the routing decision
  and delegates.
* Numeric thresholds used for pass/fail colour coding (e.g. 0.66 for
  code-generation scores) are embedded in the rendering logic here;
  changing them requires updating the corresponding scoring thresholds in
  ``execution/summary.py`` for consistency.
* Safe score coercion is owned by ``nichebench.execution.summary.normalize_score``;
  report views import it directly so display and summary aggregation agree on
  what counts as a valid score (finite numeric strings, ``bool`` rejected,
  malformed/non-finite/out-of-range values fall back to the supplied default).
"""

from nichebench.cli.rich_views.reports.completion import render_run_completion_report
from nichebench.cli.rich_views.reports.selectors import (
    render_run_list,
    render_run_selector,
)

__all__ = [
    "render_run_completion_report",
    "render_run_list",
    "render_run_selector",
]
