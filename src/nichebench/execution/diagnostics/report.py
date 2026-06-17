"""Report formatting — human-readable text output from forensics report dicts.

This module was previously part of ``core/forensics.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _fmt(value: Any) -> str:
    """Format a scalar value for the text report table."""
    if value is None:
        return "null"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def format_text_report(reports: List[Dict[str, Any]]) -> str:
    """Format a list of trial report dicts into a human-readable text block."""
    lines: List[str] = []
    total = len(reports)
    for idx, r in enumerate(reports, 1):
        lines.append(f"{'=' * 64}")
        lines.append(f"Trial {idx}/{total}: {_fmt(r.get('test_id'))}  " f"(trial_index={_fmt(r.get('trial_index'))})")
        lines.append(f"{'=' * 64}")
        lines.append(f"  Path              : {r.get('trial_path', '')}")
        lines.append(f"  Model             : {_fmt(r.get('model'))}")
        lines.append(f"  Run ID            : {_fmt(r.get('run_id'))}")

        lines.append("")
        lines.append("  [ Timing ]")
        lines.append(f"    started_at      : {_fmt(r.get('started_at'))}")
        lines.append(f"    ended_at        : {_fmt(r.get('ended_at'))}")
        dur = r.get("duration_seconds")
        lines.append(f"    duration        : {f'{dur:.1f}s' if dur is not None else 'null'}")

        lines.append("")
        lines.append("  [ Failure ]")
        lines.append(f"    failure_class   : {_fmt(r.get('failure_class'))}")
        lines.append(f"    failure_code    : {_fmt(r.get('failure_code'))}")
        lines.append(f"    first_fail_stage: {_fmt(r.get('first_failed_stage'))}")

        lines.append("")
        lines.append("  [ Scores ]")
        lines.append(f"    deterministic   : {_fmt(r.get('deterministic_score'))}")
        lines.append(f"    judge           : {_fmt(r.get('judge_score'))}")
        lines.append(f"    hybrid          : {_fmt(r.get('hybrid_score'))}")
        lines.append(f"    final           : {_fmt(r.get('final_score'))}")

        lines.append("")
        lines.append("  [ Trajectory ]")
        lines.append(f"    messages        : {_fmt(r.get('trajectory_messages'))}")
        lines.append(f"    tool_calls_total: {_fmt(r.get('tool_calls_total'))}")
        lines.append(f"    non_completed   : {_fmt(r.get('tool_calls_noncompleted'))}")
        tool_counts = r.get("tool_status_counts") or {}
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(tool_counts.items())) if tool_counts else "(none)"
        lines.append(f"    tool_breakdown  : {breakdown}")
        lines.append(f"    last_finish_rsn : {_fmt(r.get('last_assistant_finish_reason'))}")
        lines.append(f"    reasoning_total  : {_fmt(r.get('reasoning_total'))}")
        lines.append(f"    reasoning_chars  : {_fmt(r.get('reasoning_chars'))}")
        lines.append(f"    text_replies_tot : {_fmt(r.get('text_replies_total'))}")
        lines.append(f"    text_chars       : {_fmt(r.get('text_chars'))}")

        lines.append("")
        lines.append("  [ Artifacts ]")
        artifacts = r.get("artifacts") or {}
        present = [k for k, v in artifacts.items() if v]
        absent = [k for k, v in artifacts.items() if not v]
        lines.append(f"    present         : {', '.join(sorted(present)) if present else '(none)'}")
        lines.append(f"    absent          : {', '.join(sorted(absent)) if absent else '(none)'}")
        lines.append("")

    return "\n".join(lines)
