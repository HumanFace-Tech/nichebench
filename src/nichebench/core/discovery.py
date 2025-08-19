"""Auto-discover framework packs under src/frameworks."""
from pathlib import Path
from typing import Dict, List

from nichebench.core.loader_yaml import load_taskspecs_for_framework
from nichebench.core.datamodel import TaskSpec


def discover_frameworks(root_src: Path = Path(__file__).resolve().parents[2] / "frameworks") -> Dict[str, List[TaskSpec]]:
    """Discover frameworks under the repo's `src/nichebench/frameworks/` directory.

    Returns a mapping framework_name -> list[TaskSpec]
    """
    frameworks: Dict[str, List[TaskSpec]] = {}
    if not root_src.exists():
        return frameworks

    for child in sorted(root_src.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        tasks = load_taskspecs_for_framework(child, name)
        frameworks[name] = tasks
    return frameworks
