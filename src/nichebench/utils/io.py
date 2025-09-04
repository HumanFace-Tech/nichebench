"""IO helpers for results directory and saving JSONL/JSON summaries."""

import json
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
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
