"""IO helpers for results directory and saving JSONL/JSON summaries."""

import contextlib
import json
import re
from pathlib import Path
from typing import Any


def ensure_results_dir(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_jsonl(path: Path, rows: list[dict[str, Any]], mode: str = "w"):
    """Save rows to JSONL file.

    Args:
        path: File path to save to
        rows: List of dictionaries to save
        mode: File mode - 'w' for write (overwrite), 'a' for append
    """
    with path.open(mode, encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def save_json(path: Path, obj: Any):
    """Write JSON atomically via a sibling temporary file then ``os.replace``.

    A crash mid-write leaves the original file (or no file) in place rather
    than a half-written/truncated one.  This protects ``summary.json`` from
    becoming unrecoverable on SIGKILL or disk-full conditions.
    """
    import os
    import tempfile

    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup of the temp file on failure.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def strip_think_tags(text: Any) -> Any:
    """Strip <think> and </think> XML tags and their content from LLM output.

    This removes any reasoning/thinking content that the model may have included
    in <think> tags, keeping only the final response content.

    Args:
        text: The raw LLM output text

    Returns:
        The text with all <think>...</think> blocks removed, or the original
        value if it's not a string
    """
    if not text or not isinstance(text, str):
        return text

    # Remove <think>...</think> blocks (case-insensitive, multiline, non-greedy)
    # Use re.DOTALL to make . match newlines, and re.IGNORECASE for case insensitivity
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Clean up any extra whitespace that might be left behind
    cleaned = re.sub(r"\n\s*\n\s*\n", "\n\n", cleaned)  # Collapse multiple empty lines
    return cleaned.strip()
