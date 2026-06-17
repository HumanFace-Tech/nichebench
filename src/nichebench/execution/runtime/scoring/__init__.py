"""Runtime scoring package.

This package refactors ``nichebench.execution.runtime.scoring`` into discrete
modules without changing the public API.  All public types and functions are
re-exported here so that existing callers continue to work unchanged.

Package layout
--------------
datamodel.py           — ``CheckResult``, ``HybridScore`` data classes.
validation.py          — ``ValidationError``, ``validate_runtime_testcase``,
                         ``validate_container_image_pin``.
scorer.py              — ``RuntimeScorer`` orchestration + score aggregation.
check_runner.py        — dispatch logic for op-based checks.
ops_filesystem.py      — file_exists, file_glob_exists, grep_file, grep_dir, …
ops_drupal.py          — drush_output_contains, drush_watchdog_clean, …
ops_static_analysis.py — composer_script_clean, phpstan_clean.

Backward compatibility
----------------------
All symbols previously importable from
``nichebench.execution.runtime.scoring`` remain importable from this package.
The old module (``scoring.py``) is replaced by this package; no stub file is
created.

Module-level constants
----------------------
FLOATING_TAGS — frozenset of tag names considered unpinned.  Re-exported from
                ``check_runner`` for convenience.
"""

from nichebench.execution.runtime.scoring.check_runner import FLOATING_TAGS
from nichebench.execution.runtime.scoring.datamodel import CheckResult, HybridScore
from nichebench.execution.runtime.scoring.scorer import RuntimeScorer
from nichebench.execution.runtime.scoring.validation import (
    ValidationError,
    validate_container_image_pin,
    validate_runtime_testcase,
)

__all__ = [
    "CheckResult",
    "HybridScore",
    "RuntimeScorer",
    "ValidationError",
    "validate_container_image_pin",
    "validate_runtime_testcase",
    "FLOATING_TAGS",
]
