"""Forensics module — post-hoc analysis of trial and run artifacts.

This module was previously at ``core/forensics.py``.  It reads trial directories
containing ``metadata.json``, ``runtime_trace.json``, ``trajectory.json``,
``checks.json``, and ``run.log`` and produces structured report dicts that are
consumed by the CLI ``forensics`` command and by the optional LLM judge.

Post-hoc report generation role
--------------------------------
``collect_reports`` is the single entry point.  It accepts either:
  - A **trial directory** (contains metadata.json, trajectory.json, etc.)
  - A **run directory** (contains ``details.jsonl`` and a ``runtime/`` sub-tree)

Reports are assembled from all available artifact files; missing files result
in ``None`` field values rather than errors.  Consumers must handle sparse data.

Trial-dir analysis boundaries
----------------------------
- Does NOT execute checks or re-score — only reads pre-computed results.
- Does NOT classify failures — that is the remit of ``trace.classify_runtime_failure``.
- Trajectory analysis (``_analyze_trajectory``) handles both OpenCode format
  (parts-based tool calls) and generic assistant-format (tool_calls arrays).
- Run-level timing and score data is read from ``details.jsonl`` when present;
  trial-level data (from metadata.json / runtime_trace.json) takes precedence
  for fields that exist in both.

Public API
----------
collect_reports(path: Path) -> list[dict]
    Entry point.  Accepts a trial directory or a run directory.
"""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    """Load a JSON file, returning None on any error (no traceback)."""
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))  # type: ignore[return-value]
    except Exception:
        return None


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file, skipping malformed lines."""
    rows: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                pass
    except Exception:
        pass
    return rows


def _duration_from_iso(started_at: Optional[str], ended_at: Optional[str]) -> Optional[float]:
    """Return elapsed seconds from two ISO-8601 strings, or None."""
    if not started_at or not ended_at:
        return None
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        return (e - s).total_seconds()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Path classification helpers
# ---------------------------------------------------------------------------

_TRIAL_MARKER_FILES = ("metadata.json", "runtime_trace.json", "trajectory.json", "run.log")


def _is_trial_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any((path / f).exists() for f in _TRIAL_MARKER_FILES)


def _is_run_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (path / "details.jsonl").exists() or (path / "runtime").is_dir()


# ---------------------------------------------------------------------------
# Trajectory analysis
# ---------------------------------------------------------------------------


def _analyze_trajectory(traj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract counts and finish reason from a trajectory dict."""
    out: Dict[str, Any] = {
        "messages": None,
        "tool_calls_total": None,
        "tool_calls_noncompleted": None,
        "tool_status_counts": None,
        "last_assistant_finish_reason": None,
        "reasoning_total": None,
        "reasoning_chars": None,
        "text_replies_total": None,
        "text_chars": None,
    }
    if not isinstance(traj, dict):
        return out

    messages = traj.get("messages")
    if not isinstance(messages, list):
        return out

    out["messages"] = len(messages)

    total_tool_calls = 0
    noncompleted_tool_calls = 0
    tool_status_counts: Dict[str, int] = {}
    reasoning_total = 0
    reasoning_chars = 0
    text_replies_total = 0
    text_chars = 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        # OpenCode format: tool calls are represented as parts with state/status.
        parts = msg.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "tool":
                    total_tool_calls += 1
                    status = str((part.get("state") or {}).get("status") or "unknown")
                    tool_status_counts[status] = tool_status_counts.get(status, 0) + 1
                    if status != "completed":
                        noncompleted_tool_calls += 1
                elif ptype == "reasoning":
                    reasoning_total += 1
                    reasoning_chars += len(part.get("text", ""))
                elif ptype == "text":
                    text_replies_total += 1
                    text_chars += len(part.get("text", ""))

        # Fallback for non-OpenCode trajectories with assistant tool_calls arrays.
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                total_tool_calls += 1
                tool_status_counts["unknown"] = tool_status_counts.get("unknown", 0) + 1
                noncompleted_tool_calls += 1

    out["tool_calls_total"] = total_tool_calls
    out["tool_calls_noncompleted"] = noncompleted_tool_calls
    out["tool_status_counts"] = tool_status_counts
    out["reasoning_total"] = reasoning_total
    out["reasoning_chars"] = reasoning_chars
    out["text_replies_total"] = text_replies_total
    out["text_chars"] = text_chars

    # Last assistant finish / stop reason
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue

        # Direct field (OpenAI / custom)
        reason = msg.get("finish_reason") or msg.get("stop_reason")

        # Check parts list for a step-finish part (OpenCode format)
        if not reason:
            parts = msg.get("parts")
            if isinstance(parts, list):
                for part in reversed(parts):
                    if isinstance(part, dict) and part.get("type") == "step-finish":
                        reason = part.get("reason") or part.get("finishReason") or part.get("finish_reason")
                        if reason:
                            break

        out["last_assistant_finish_reason"] = reason
        break

    return out


# ---------------------------------------------------------------------------
# Single trial analysis
# ---------------------------------------------------------------------------


def _analyze_trial_dir(trial_dir: Path) -> Dict[str, Any]:
    """Return a report dict for a single trial directory."""
    report: Dict[str, Any] = {
        "trial_path": str(trial_dir),
        "model": None,
        "test_id": None,
        "run_id": None,
        "trial_index": None,
        "started_at": None,
        "ended_at": None,
        "duration_seconds": None,
        "failure_class": None,
        "failure_code": None,
        "first_failed_stage": None,
        "deterministic_score": None,
        "judge_score": None,
        "hybrid_score": None,
        "final_score": None,
        "trajectory_messages": None,
        "tool_calls_total": None,
        "tool_calls_noncompleted": None,
        "tool_status_counts": None,
        "last_assistant_finish_reason": None,
        "reasoning_total": None,
        "reasoning_chars": None,
        "text_replies_total": None,
        "text_chars": None,
        "artifacts": {
            "run.log": False,
            "trajectory.json": False,
            "opencode_partial_trajectory.json": False,
            "opencode_session_dump.json": False,
            "checks.json": False,
        },
    }

    # Artifact presence (file-existence booleans)
    for artifact_name in list(report["artifacts"]):
        report["artifacts"][artifact_name] = (trial_dir / artifact_name).exists()

    # --- metadata.json ---
    metadata = _load_json(trial_dir / "metadata.json")
    if isinstance(metadata, dict):
        report["model"] = metadata.get("mut_model_binding") or metadata.get("opencode_model_binding")
        report["failure_class"] = metadata.get("failure_class")
        report["failure_code"] = metadata.get("failure_code")
        report["first_failed_stage"] = metadata.get("first_failed_stage")
        if metadata.get("trial") is not None:
            report["trial_index"] = metadata.get("trial")

    # --- runtime_trace.json ---
    runtime_trace = _load_json(trial_dir / "runtime_trace.json")
    if isinstance(runtime_trace, dict):
        if report["test_id"] is None:
            report["test_id"] = runtime_trace.get("test_id")
        report["started_at"] = runtime_trace.get("started_at")
        report["ended_at"] = runtime_trace.get("ended_at")
        report["duration_seconds"] = _duration_from_iso(report["started_at"], report["ended_at"])
        # first_failed_stage from stages list if not already in metadata
        if report["first_failed_stage"] is None:
            for stage_entry in runtime_trace.get("stages", []):
                if isinstance(stage_entry, dict) and stage_entry.get("status") == "failed":
                    report["first_failed_stage"] = stage_entry.get("stage")
                    break

    # --- trajectory.json (fallback to partial) ---
    traj = _load_json(trial_dir / "trajectory.json")
    if traj is None:
        traj = _load_json(trial_dir / "opencode_partial_trajectory.json")

    traj_info = _analyze_trajectory(traj)
    report["trajectory_messages"] = traj_info["messages"]
    report["tool_calls_total"] = traj_info["tool_calls_total"]
    report["tool_calls_noncompleted"] = traj_info["tool_calls_noncompleted"]
    report["tool_status_counts"] = traj_info["tool_status_counts"]
    report["last_assistant_finish_reason"] = traj_info["last_assistant_finish_reason"]
    report["reasoning_total"] = traj_info["reasoning_total"]
    report["reasoning_chars"] = traj_info["reasoning_chars"]
    report["text_replies_total"] = traj_info["text_replies_total"]
    report["text_chars"] = traj_info["text_chars"]

    # Fallback timing fields from trajectory stats
    if isinstance(traj, dict):
        stats = traj.get("stats") or {}
        if isinstance(stats, dict) and report["duration_seconds"] is None:
            report["duration_seconds"] = stats.get("duration_seconds")
        if report["started_at"] is None:
            report["started_at"] = traj.get("created_at")
        if report["ended_at"] is None:
            report["ended_at"] = traj.get("ended_at")
        if report["test_id"] is None:
            report["test_id"] = traj.get("instance_id")
        if report["model"] is None:
            report["model"] = traj.get("model")

    # --- checks.json (deterministic score fallback) ---
    checks_data = _load_json(trial_dir / "checks.json")
    if isinstance(checks_data, dict) and report["deterministic_score"] is None:
        det = checks_data.get("deterministic")
        if isinstance(det, list) and det:
            passed = sum(1 for c in det if isinstance(c, dict) and c.get("passed"))
            report["deterministic_score"] = passed / len(det)

    # Infer trial_index from directory name (trial_N) when not in metadata
    if report["trial_index"] is None and trial_dir.name.startswith("trial_"):
        with contextlib.suppress(ValueError, IndexError):
            report["trial_index"] = int(trial_dir.name.split("_", 1)[1])

    # For single-trial directories without trial_N naming, default to trial 1.
    if report["trial_index"] is None:
        report["trial_index"] = 1

    # Infer test_id from parent directory structure when still missing
    if report["test_id"] is None:
        parent = trial_dir.parent
        if parent.name.startswith("trial_"):
            # trial_N inside test_id dir
            report["test_id"] = parent.parent.name
        elif parent.name == "runtime":
            report["test_id"] = trial_dir.name
        else:
            report["test_id"] = parent.name

    return report


# ---------------------------------------------------------------------------
# Run-dir helpers
# ---------------------------------------------------------------------------


def _find_trial_dirs(run_dir: Path) -> List[Path]:
    """Return all trial directories contained in a run directory, sorted."""
    trial_dirs: List[Path] = []
    runtime_dir = run_dir / "runtime"
    if not runtime_dir.exists():
        return trial_dirs

    for test_id_dir in sorted(runtime_dir.iterdir()):
        if not test_id_dir.is_dir():
            continue

        # Case 1: test_id_dir itself is a trial dir (single-trial run)
        if _is_trial_dir(test_id_dir):
            trial_dirs.append(test_id_dir)
            continue

        # Case 2: trial_N subdirectories
        subdirs = sorted(
            [d for d in test_id_dir.iterdir() if d.is_dir() and d.name.startswith("trial_")],
            key=lambda d: _trial_num(d.name),
        )
        trial_dirs.extend(subdirs)

    return trial_dirs


def _trial_num(name: str) -> int:
    try:
        return int(name.split("_", 1)[1])
    except (ValueError, IndexError):
        return 0


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_details_index(run_dir: Path) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """
    Parse details.jsonl and return a dict keyed by (test_id, trial_index).
    Each value holds scores and failure info extracted from that JSONL row.
    """
    index: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in _load_jsonl(run_dir / "details.jsonl"):
        test_id = row.get("test_id") or ""
        trial_idx = _safe_int(row.get("trial"), 1)

        judge_output = row.get("judge_output")
        if not isinstance(judge_output, dict):
            judge_output = {}

        index[(test_id, trial_idx)] = {
            "model": row.get("mut_model"),
            # details.jsonl top-level score fields (written by TestResult.to_dict)
            "deterministic_score": row.get("deterministic_score"),
            "judge_score": row.get("judge_score"),
            # "final_score" in details.jsonl is judge_output["hybrid_score"]  # noqa: ERA001
            "hybrid_score": row.get("final_score"),
            "final_score": row.get("final_score"),
            # Failure fields from judge_output sub-dict
            "failure_class": judge_output.get("failure_class"),
            "failure_code": judge_output.get("failure_code"),
            "first_failed_stage": judge_output.get("first_failed_stage"),
        }
    return index


def _infer_run_id(trial_dir: Path) -> Optional[str]:
    """Walk upward from a trial dir to find the run-level directory name."""
    p = trial_dir
    for _ in range(5):
        p = p.parent
        if p == p.parent:
            break
        if (p / "details.jsonl").exists() or (p / "summary.json").exists():
            return p.name
    return None


# ---------------------------------------------------------------------------
# High-level collection  (public API)
# ---------------------------------------------------------------------------


def collect_reports(path: Path) -> List[Dict[str, Any]]:
    """Collect trial report dicts from a trial dir or a run dir.

    Returns an empty list (and prints to stderr) if path is not recognised.
    """
    reports: List[Dict[str, Any]] = []

    if _is_trial_dir(path):
        report = _analyze_trial_dir(path)
        report["run_id"] = _infer_run_id(path)
        reports.append(report)

    elif _is_run_dir(path):
        details_index = _load_details_index(path)
        run_id = path.name

        for trial_dir in _find_trial_dirs(path):
            report = _analyze_trial_dir(trial_dir)
            report["run_id"] = run_id

            # Supplement with details.jsonl data for fields still missing
            test_id = report.get("test_id") or ""
            trial_idx = _safe_int(report.get("trial_index"), 1)
            row_data = details_index.get((test_id, trial_idx), {})

            for field in (
                "model",
                "deterministic_score",
                "judge_score",
                "hybrid_score",
                "final_score",
                "failure_class",
                "failure_code",
                "first_failed_stage",
            ):
                if report[field] is None and row_data.get(field) is not None:
                    report[field] = row_data[field]

            reports.append(report)

    else:
        print(
            f"[forensics] ERROR: {path} is not a recognisable trial or run directory.",
            file=sys.stderr,
        )

    return reports
