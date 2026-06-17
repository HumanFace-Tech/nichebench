"""Extraction helpers for solver output parsing.

These functions extract structured data (summaries, filenames) from the
solver's free-form text output. They are pure functions with no side
effects and no external dependencies beyond ``re``.

Ownership
=========
This module is owned by the ``langgraph_code_agent`` package. It is
called exclusively by ``solver.py`` after each solver node invocation.
"""

import re
from typing import List


def extract_summary(text: str) -> str:
    """Return the content after ``SUMMARY:`` in solver output, or a fallback string.

    Parsing is regex-based against ``SUMMARY: ...`` (case-insensitive, stops
    at the next ``\\n\\n`` or all-caps section header).

    Args:
        text: raw solver output string.

    Returns:
        The extracted summary text, or ``"Step completed (no summary found)"``
        if the pattern does not match.
    """
    pattern = r"SUMMARY:\s*\n(.*?)(?:\n\n|\n[A-Z]+:|$)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "Step completed (no summary found)"


def extract_filenames(text: str) -> List[str]:
    """Scan solver output for filenames in ``FILENAME:``, ``File:``,
    ``Creating:``, or ``Modifying:`` patterns.

    Deduplication is performed so each unique path appears at most once.
    If no patterns match, an empty list is returned.

    Args:
        text: raw solver output string.

    Returns:
        List of unique file paths found in the text.
    """
    filenames: List[str] = []

    # Look for patterns like "FILENAME: path/to/file.ext" or "File: path/to/file.ext"
    filename_patterns = [
        r"FILENAME:\s*([^\n]+)",
        r"File:\s*([^\n]+)",
        r"Creating:\s*([^\n]+)",
        r"Modifying:\s*([^\n]+)",
    ]

    for pattern in filename_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            filename = match.strip()
            if filename and filename not in filenames:
                filenames.append(filename)

    return filenames
