"""Result persistence helpers for NicheBench test execution.

This module owns:
    - setup_results_directory: create results directory tree
    - save_incremental_result: append TestResult to details.jsonl

This module does NOT own:
    - TestExecutor orchestration (see orchestrator.py)
    - Category routing (see dispatch.py)
    - Summary aggregation (see summary.py)
    - Parallel execution (see parallel.py)
    - Runtime artifact persistence (see RuntimeExecutionMixin._save_runtime_artifacts)
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from uuid import uuid4

from nichebench.execution.result import TestResult
from nichebench.utils.io import ensure_results_dir, save_jsonl


def setup_results_directory(
    results_config: Dict[str, Any],
    framework: str,
    category: str,
    mut_model_str: str,
) -> Tuple[Path, Path, Path]:
    """Create the results directory tree and return paths.

    Directory structure is
    ``results/<framework>/<category>/<model-str>/<timestamp>-<short-uuid>/``.

    A short UUID suffix is appended to the timestamp to guarantee that two
    invocations started in the same timestamp bucket do not share
    ``details.jsonl`` / ``summary.json``.  The timestamp is retained for
    human readability and the UUID is short (8 hex chars) so the directory
    name stays compact.

    Args:
        results_config: Results configuration dict with timestamp_format.
        framework: Framework name (e.g., "drupal").
        category: Task category.
        mut_model_str: MUT model string for path construction.

    Returns:
        Tuple of (details_path, summary_path, outdir):
        - details_path: details.jsonl — append-only per-result stream
        - summary_path: summary.json — aggregate statistics
        - outdir: root results directory
    """
    timestamp = datetime.now().strftime(results_config["timestamp_format"])
    short_uuid = uuid4().hex[:8]
    outdir = Path("results") / framework / category / mut_model_str.replace("/", "-") / f"{timestamp}-{short_uuid}"
    ensure_results_dir(outdir)

    details_path = outdir / "details.jsonl"
    summary_path = outdir / "summary.json"

    return details_path, summary_path, outdir


def save_incremental_result(
    result: TestResult,
    details_path: Path,
    save_runtime_artifacts_fn: Optional[Callable[[TestResult], None]] = None,
) -> None:
    """Append a single TestResult to the details.jsonl file.

    Runtime artifacts are persisted *before* the JSONL append so that a crash
    between the two writes leaves the on-disk artifact bundle intact and
    recoverable, rather than a details.jsonl row that advertises artifacts
    that were never written.  If artifact persistence fails, the JSONL
    append is skipped so the details stream does not advertise a partial
    bundle.

    Args:
        result: Completed TestResult to serialize.
        details_path: Path to the details.jsonl file.
        save_runtime_artifacts_fn: Optional callable that saves runtime artifacts.
            When provided, it is called with (result,) to persist runtime artifacts.
    """
    if save_runtime_artifacts_fn:
        save_runtime_artifacts_fn(result)
    save_jsonl(details_path, [result.to_dict()], mode="a")
