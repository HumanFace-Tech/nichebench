"""Focused validation artifact extraction.

This module only extracts text diagnostics from deterministic check results for
artifact persistence. It does not run checks or manage runtime output files.
"""

from __future__ import annotations

from typing import Any, Dict


def extract_validation_artifacts(check_results: Any) -> Dict[str, str]:
    """Extract focused validation artifacts from deterministic check results.

    Writes PHPStan, PHPCS, and watchdog errors to text files so they can be
    included in the artifact bundle without serialising complex nested objects.
    """
    artifacts: Dict[str, str] = {}
    if not check_results:
        return artifacts

    for result in check_results:
        if not hasattr(result, "details"):
            continue
        details = result.details or {}

        if "phpcs" in result.name.lower() and details.get("stdout"):
            artifacts["last_phpcs.txt"] = details["stdout"][:20000]
        elif "phpstan" in result.name.lower() and (details.get("stdout") or details.get("stderr")):
            artifacts["last_phpstan.txt"] = (details.get("stderr") or details.get("stdout") or "")[:20000]
        elif result.type == "drush_watchdog_clean" and not result.passed:
            artifacts["watchdog_errors.txt"] = result.message

    return artifacts
