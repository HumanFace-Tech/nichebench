"""Helpers for loading prompt text from YAML files."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@lru_cache(maxsize=128)
def load_prompt_mapping(path: Path) -> Dict[str, Any]:
    """Load a YAML mapping from ``path`` for prompt lookup."""
    if not path.exists():
        return {}
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def load_prompt_text(path: Path, key: str, default: Optional[str] = None) -> Optional[str]:
    """Load string prompt text by key from a YAML file."""
    value = load_prompt_mapping(path).get(key)
    if isinstance(value, str):
        return value
    return default
