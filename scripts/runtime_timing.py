#!/usr/bin/env python3
"""Post-hoc timing estimator for NicheBench runtime runs.

Reads artifacts a runtime run already produced - the capture archive journals,
the OpenCode session-store snapshot, and the runtime trace - and estimates where
wall-clock time went. Nothing is re-executed, no provider is contacted, and no
score is touched.

Usage:
    python scripts/runtime_timing.py --result-dir <task_dir> [--json] [--top N]
    python scripts/runtime_timing.py --capture <capture_dir> [--json] [--top N]

Metric semantics (also documented in docs/runtime/runtime-tracing.md):
- model request time is round-trip wall time: provider compute + network +
  streaming. It is not inference-only and not GPU telemetry.
- idle/queue gaps are the interval between a response completion and the next
  request sent. They are not GPU wait time.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

HTTP_REQUEST_SENT = "http_request_sent"
HTTP_RESPONSE_COMPLETE = "http_response_complete"

BASE_NOTES = [
    "Model request time is round-trip wall time (provider compute + network + streaming). "
    "It is not provider-side inference time and not GPU telemetry.",
    "idle_or_queue_gap is the interval between a response completion and the next request sent. "
    "It is not GPU wait time; tool execution and agent-side reasoning both land inside it.",
    "Tool time comes from opencode.db part state.time and covers tool execution only.",
    "Unaccounted time is wall span minus model request time minus idle/queue gaps. "
    "It covers setup, checks, scoring, and any time outside the request/gap sequence.",
    "Percentiles use the nearest-rank method (no interpolation).",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp. Returns None when the value is unusable."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from datetime import timezone

        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile of an already sorted list."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    rank = math.ceil(q * len(values))
    rank = min(max(rank, 1), len(values))
    return values[rank - 1]


def summarize_durations(values: list[float]) -> dict[str, Any]:
    """Summarize a list of durations (seconds)."""
    if not values:
        return {
            "count": 0,
            "total_seconds": 0.0,
            "mean_seconds": 0.0,
            "p50_seconds": 0.0,
            "p95_seconds": 0.0,
            "min_seconds": 0.0,
            "max_seconds": 0.0,
        }
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "total_seconds": round(sum(ordered), 6),
        "mean_seconds": round(statistics.fmean(ordered), 6),
        "p50_seconds": round(percentile(ordered, 0.50), 6),
        "p95_seconds": round(percentile(ordered, 0.95), 6),
        "min_seconds": round(ordered[0], 6),
        "max_seconds": round(ordered[-1], 6),
    }


def read_json(path: Path) -> Any | None:
    """Read a JSON document. Returns None when the file is missing or broken."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def read_journal(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read a JSONL journal.

    Malformed lines are skipped, never raised on. Returns the parsed records and
    the number of skipped lines.
    """
    records: list[dict[str, Any]] = []
    malformed = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except ValueError:
                    malformed += 1
                    continue
                if isinstance(record, dict):
                    records.append(record)
                else:
                    malformed += 1
    except OSError:
        return [], 0
    return records, malformed


# ---------------------------------------------------------------------------
# HTTP journal analysis
# ---------------------------------------------------------------------------


def analyze_http_journal(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Pair HTTP requests with responses in one journal and derive timings."""
    sent: dict[str, datetime] = {}
    completed: dict[str, datetime] = {}
    duplicate_sent = 0
    duplicate_completed = 0
    skipped = 0

    for record in records:
        event = record.get("event")
        if event not in (HTTP_REQUEST_SENT, HTTP_RESPONSE_COMPLETE):
            continue
        request_id = record.get("request_id")
        timestamp = parse_timestamp(record.get("timestamp"))
        if not isinstance(request_id, str) or not request_id or timestamp is None:
            skipped += 1
            continue
        if event == HTTP_REQUEST_SENT:
            if request_id in sent:
                duplicate_sent += 1
                continue
            sent[request_id] = timestamp
        else:
            if request_id in completed:
                duplicate_completed += 1
                continue
            completed[request_id] = timestamp

    durations: list[float] = []
    incomplete: list[str] = []
    invalid = 0
    for request_id, started in sent.items():
        finished = completed.get(request_id)
        if finished is None:
            incomplete.append(request_id)
            continue
        delta = (finished - started).total_seconds()
        if delta < 0:
            invalid += 1
            continue
        durations.append(delta)

    orphan_responses = [rid for rid in completed if rid not in sent]

    # Gaps: response completion -> next request sent, in timestamp order.
    ordered_sent = sorted(sent.items(), key=lambda item: item[1])
    gaps: list[float] = []
    for index in range(len(ordered_sent) - 1):
        request_id, _ = ordered_sent[index]
        finished = completed.get(request_id)
        if finished is None:
            continue
        next_started = ordered_sent[index + 1][1]
        gap = (next_started - finished).total_seconds()
        if gap > 0:
            gaps.append(gap)

    summary = summarize_durations(durations)
    summary.update(
        {
            "incomplete_pairs": len(incomplete),
            "incomplete_request_ids": sorted(incomplete),
            "orphan_responses": len(orphan_responses),
            "invalid_durations": invalid,
            "skipped_records": skipped,
            "duplicate_sent": duplicate_sent,
            "duplicate_completed": duplicate_completed,
        }
    )
    return {
        "requests": summary,
        "durations": durations,
        "gaps": gaps,
        "gap_summary": summarize_durations(gaps),
        "first_timestamp": min(sent.values()) if sent else None,
        "last_timestamp": max(
            list(sent.values()) + list(completed.values()),
            default=None,
        ),
    }


def analyze_journals(capture_dir: Path | None) -> dict[str, Any]:
    """Analyze every attempt and judge journal under the capture archive."""
    lanes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    journal_reports: list[dict[str, Any]] = []
    malformed_total = 0

    if capture_dir is None or not capture_dir.is_dir():
        return {"lanes": {}, "journals": [], "malformed_lines": 0, "span": None}

    for lane, folder in (("attempt", "attempts"), ("judge", "judges")):
        lane_root = capture_dir / folder
        if not lane_root.is_dir():
            continue
        for journal in sorted(lane_root.glob("*/events.jsonl")):
            records, malformed = read_journal(journal)
            malformed_total += malformed
            analysis = analyze_http_journal(records)
            lanes[lane].append(analysis)
            journal_reports.append(
                {
                    "lane": lane,
                    "journal": str(journal),
                    "malformed_lines": malformed,
                    "requests": analysis["requests"],
                    "idle_or_queue_gap": analysis["gap_summary"],
                }
            )

    lane_summaries: dict[str, Any] = {}
    all_durations: list[float] = []
    all_gaps: list[float] = []
    all_incomplete = 0
    all_orphans = 0
    all_skipped = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    for lane, analyses in lanes.items():
        durations: list[float] = []
        gaps: list[float] = []
        incomplete = 0
        orphans = 0
        skipped = 0
        for analysis in analyses:
            durations.extend(analysis["durations"])
            gaps.extend(analysis["gaps"])
            incomplete += analysis["requests"]["incomplete_pairs"]
            orphans += analysis["requests"]["orphan_responses"]
            skipped += analysis["requests"]["skipped_records"]
            start = analysis["first_timestamp"]
            end = analysis["last_timestamp"]
            if start is not None and (first_ts is None or start < first_ts):
                first_ts = start
            if end is not None and (last_ts is None or end > last_ts):
                last_ts = end
        lane_summary = summarize_durations(durations)
        lane_summary.update(
            {
                "incomplete_pairs": incomplete,
                "orphan_responses": orphans,
                "idle_or_queue_gap": summarize_durations(gaps),
            }
        )
        lane_summaries[lane] = lane_summary
        all_durations.extend(durations)
        all_gaps.extend(gaps)
        all_incomplete += incomplete
        all_orphans += orphans
        all_skipped += skipped

    total = summarize_durations(all_durations)
    total.update(
        {
            "incomplete_pairs": all_incomplete,
            "orphan_responses": all_orphans,
            "skipped_records": all_skipped,
            "malformed_lines": malformed_total,
            "idle_or_queue_gap": summarize_durations(all_gaps),
            "lanes": lane_summaries,
        }
    )

    span = None
    if first_ts is not None and last_ts is not None:
        span = (last_ts - first_ts).total_seconds()

    return {
        "lanes": lane_summaries,
        "journals": journal_reports,
        "malformed_lines": malformed_total,
        "span": span,
        "total": total,
    }


# ---------------------------------------------------------------------------
# OpenCode session store analysis
# ---------------------------------------------------------------------------


def analyze_tool_db(db_path: Path | None) -> dict[str, Any]:
    """Derive tool execution timings from the OpenCode session-store snapshot."""
    result: dict[str, Any] = {
        "count": 0,
        "total_seconds": 0.0,
        "mean_seconds": 0.0,
        "p50_seconds": 0.0,
        "p95_seconds": 0.0,
        "min_seconds": 0.0,
        "max_seconds": 0.0,
        "missing_timing": 0,
        "malformed_rows": 0,
        "by_tool": {},
        "top_tools": [],
        "db_path": str(db_path) if db_path else None,
        "available": False,
    }
    if db_path is None or not db_path.is_file():
        return result

    durations: list[float] = []
    per_tool: dict[str, list[float]] = defaultdict(list)
    missing_timing = 0
    malformed_rows = 0

    uri = "file:" + urllib.parse.quote(str(db_path.resolve())) + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return result

    readable = True
    try:
        cursor = connection.execute("select data from part")
        rows = cursor.fetchall()
    except sqlite3.Error:
        readable = False
        rows = []
    finally:
        connection.close()

    try:
        for row in rows:
            raw = row[0] if row else None
            if not isinstance(raw, str):
                malformed_rows += 1
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                malformed_rows += 1
                continue
            if not isinstance(data, dict):
                malformed_rows += 1
                continue
            if data.get("type") != "tool":
                continue
            state = data.get("state")
            timing = state.get("time") if isinstance(state, dict) else None
            start = timing.get("start") if isinstance(timing, dict) else None
            end = timing.get("end") if isinstance(timing, dict) else None
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                missing_timing += 1
                continue
            seconds = (float(end) - float(start)) / 1000.0
            if seconds < 0:
                missing_timing += 1
                continue
            name = data.get("tool")
            if not isinstance(name, str) or not name:
                name = "unknown"
            durations.append(seconds)
            per_tool[name].append(seconds)
    except Exception:  # noqa: BLE001 - a broken snapshot must not break the report
        pass

    summary = summarize_durations(durations)
    by_tool: dict[str, Any] = {}
    for name, values in per_tool.items():
        tool_summary = summarize_durations(values)
        by_tool[name] = tool_summary
    ordered_tools = sorted(by_tool.items(), key=lambda item: item[1]["total_seconds"], reverse=True)

    result.update(summary)
    result.update(
        {
            "missing_timing": missing_timing,
            "malformed_rows": malformed_rows,
            "by_tool": dict(
                sorted(by_tool.items(), key=lambda item: item[1]["total_seconds"], reverse=True)  # type: ignore[call-overload]
            ),
            "top_tools": [
                {"tool": name, **by_tool[name]} for name, _ in ordered_tools
            ],
            "available": readable,
        }
    )
    return result


# ---------------------------------------------------------------------------
# Runtime trace analysis
# ---------------------------------------------------------------------------


def analyze_trace(trace_path: Path | None) -> tuple[list[dict[str, Any]], float | None]:
    """Return stage durations and the trace wall span."""
    if trace_path is None:
        return [], None
    data = read_json(trace_path)
    if not isinstance(data, dict):
        return [], None
    stages: list[dict[str, Any]] = []
    raw_stages = data.get("stages")
    if isinstance(raw_stages, list):
        for raw in raw_stages:
            if not isinstance(raw, dict):
                continue
            started = parse_timestamp(raw.get("started_at"))
            ended = parse_timestamp(raw.get("ended_at"))
            duration = None
            if started is not None and ended is not None:
                duration = round((ended - started).total_seconds(), 6)
            stages.append(
                {
                    "stage": raw.get("stage"),
                    "status": raw.get("status"),
                    "started_at": raw.get("started_at"),
                    "ended_at": raw.get("ended_at"),
                    "duration_seconds": duration,
                }
            )
    wall = None
    started = parse_timestamp(data.get("started_at"))
    ended = parse_timestamp(data.get("ended_at"))
    if started is not None and ended is not None:
        wall = round((ended - started).total_seconds(), 6)
    return stages, wall


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def resolve_capture_dir(raw_path: str, result_dir: Path) -> Path | None:
    """Resolve a capture path recorded in capture.json.

    The stored path is relative to the repo working directory. Fall back to the
    result directory and the repository root before giving up.
    """
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    repo_root = Path(__file__).resolve().parents[1]
    for base in (Path.cwd(), result_dir, repo_root):
        resolved = base / candidate
        if resolved.exists():
            return resolved.resolve()
    return None


def build_report(
    *,
    capture_dir: Path | None,
    trace_path: Path | None,
    result_dir: Path | None,
    top: int,
) -> dict[str, Any]:
    """Build the timing report from whatever artifacts are available."""
    notes: list[str] = list(BASE_NOTES)

    if capture_dir is None:
        notes.append("Capture archive not found; only runtime trace data is reported.")
    elif not capture_dir.is_dir():
        notes.append(f"Capture path is not a directory: {capture_dir}")
        capture_dir = None

    journals = analyze_journals(capture_dir)

    db_path = None
    if capture_dir is not None:
        attempts_root = capture_dir / "attempts"
        if attempts_root.is_dir():
            db_candidates = sorted(attempts_root.glob("*/opencode-db/opencode.db"))
            if db_candidates:
                db_path = db_candidates[0]
            else:
                notes.append("No opencode.db snapshot found under attempts/; tool timings are unavailable.")
        else:
            notes.append("Capture archive has no attempts/ directory; tool timings are unavailable.")

    tools = analyze_tool_db(db_path)
    if not tools["available"] and capture_dir is not None:
        notes.append("Tool timings unavailable: no readable opencode.db snapshot.")

    stages, trace_wall = analyze_trace(trace_path)
    if trace_path is None:
        notes.append("runtime_trace.json not found; stage durations and wall span are unavailable.")
    elif not trace_path.is_file():
        notes.append(f"runtime_trace.json not found: {trace_path}")
    elif not stages and trace_wall is None:
        notes.append(f"runtime_trace.json could not be parsed: {trace_path}")

    if trace_wall is not None:
        wall_seconds = trace_wall
        wall_source = "runtime_trace"
    elif journals["span"] is not None:
        wall_seconds = round(journals["span"], 6)
        wall_source = "journal_span"
        notes.append("Wall span derived from journal timestamps because the runtime trace was unavailable.")
    else:
        wall_seconds = None
        wall_source = "unknown"

    model_requests = journals.get("total")
    if model_requests is None:
        model_requests = summarize_durations([])
        model_requests.update(
            {
                "incomplete_pairs": 0,
                "orphan_responses": 0,
                "skipped_records": 0,
                "malformed_lines": 0,
                "idle_or_queue_gap": summarize_durations([]),
                "lanes": {},
            }
        )

    request_total = model_requests["total_seconds"]
    gap_total = model_requests["idle_or_queue_gap"]["total_seconds"]
    unaccounted = None
    if wall_seconds is not None:
        unaccounted = round(max(wall_seconds - request_total - gap_total, 0.0), 6)

    tools = dict(tools)
    tools["top_tools"] = tools["top_tools"][:top]

    report: dict[str, Any] = {
        "source": {
            "result_dir": str(result_dir) if result_dir else None,
            "capture_dir": str(capture_dir) if capture_dir else None,
            "trace_path": str(trace_path) if trace_path else None,
        },
        "wall_seconds": wall_seconds,
        "wall_source": wall_source,
        "model_requests": model_requests,
        "tool_calls": tools,
        "stages": stages,
        "journals": journals["journals"],
        "unaccounted_seconds": unaccounted,
        "notes": notes,
    }
    return report


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def render_text(report: dict[str, Any], top: int) -> str:
    """Render the report as human-readable text."""
    lines: list[str] = ["Runtime timing estimate", "======================="]

    source = report["source"]
    if source.get("result_dir"):
        lines.append(f"Result dir : {source['result_dir']}")
    if source.get("capture_dir"):
        lines.append(f"Capture    : {source['capture_dir']}")
    if source.get("trace_path"):
        lines.append(f"Trace      : {source['trace_path']}")
    lines.append("")

    lines.append("Wall clock")
    lines.append(f"  wall span         : {_fmt(report['wall_seconds'])} s  (source: {report['wall_source']})")
    lines.append(f"  unaccounted       : {_fmt(report['unaccounted_seconds'])} s")
    lines.append("")

    requests = report["model_requests"]
    lines.append("Model requests (round trip)")
    lines.append(
        f"  count             : {requests['count']} complete, "
        f"{requests['incomplete_pairs']} incomplete, "
        f"{requests['orphan_responses']} orphan responses"
    )
    lines.append(
        f"  total / mean      : {_fmt(requests['total_seconds'])} s / {_fmt(requests['mean_seconds'])} s"
    )
    lines.append(
        f"  p50 / p95         : {_fmt(requests['p50_seconds'])} s / {_fmt(requests['p95_seconds'])} s"
    )
    lines.append(
        f"  min / max         : {_fmt(requests['min_seconds'])} s / {_fmt(requests['max_seconds'])} s"
    )
    for lane, lane_summary in sorted(requests.get("lanes", {}).items()):
        lines.append(
            f"  lane {lane:<13}: {lane_summary['count']} requests, "
            f"{_fmt(lane_summary['total_seconds'])} s total, "
            f"{lane_summary['incomplete_pairs']} incomplete"
        )
    lines.append(
        f"  skipped records   : {requests['skipped_records']} unusable, "
        f"{requests['malformed_lines']} malformed journal lines"
    )
    lines.append("")

    gaps = requests["idle_or_queue_gap"]
    lines.append("Idle / queue gaps (response -> next request)")
    lines.append(
        f"  count             : {gaps['count']} "
        f"(total {_fmt(gaps['total_seconds'])} s, mean {_fmt(gaps['mean_seconds'])} s, "
        f"p50 {_fmt(gaps['p50_seconds'])} s, p95 {_fmt(gaps['p95_seconds'])} s, "
        f"max {_fmt(gaps['max_seconds'])} s)"
    )
    lines.append("")

    tools = report["tool_calls"]
    lines.append("Tool calls (opencode.db)")
    lines.append(
        f"  count             : {tools['count']} "
        f"(total {_fmt(tools['total_seconds'])} s, mean {_fmt(tools['mean_seconds'])} s, "
        f"p50 {_fmt(tools['p50_seconds'])} s, p95 {_fmt(tools['p95_seconds'])} s, "
        f"missing timing {tools['missing_timing']})"
    )
    lines.append(f"  malformed rows    : {tools['malformed_rows']}")
    top_tools = tools.get("top_tools", [])
    if top_tools:
        lines.append("")
        lines.append(f"  top {min(top, len(top_tools))} tools by total time:")
        for entry in top_tools:
            lines.append(
                f"    {entry['tool']:<12} {entry['count']:>5} calls  "
                f"total {_fmt(entry['total_seconds'])} s  mean {_fmt(entry['mean_seconds'])} s  "
                f"p95 {_fmt(entry['p95_seconds'])} s"
            )
    else:
        lines.append("  (no tool timings available)")
    lines.append("")

    stages = report.get("stages", [])
    lines.append("Stages")
    if stages:
        for stage in stages:
            lines.append(
                f"  {str(stage.get('stage')):<26} {_fmt(stage.get('duration_seconds'))} s  "
                f"{stage.get('status') or 'unknown'}"
            )
    else:
        lines.append("  (no stage data available)")
    lines.append("")

    lines.append("Notes")
    for note in report.get("notes", []):
        lines.append(f"  - {note}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate where runtime wall-clock time went, from existing artifacts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--result-dir",
        type=Path,
        metavar="PATH",
        help="Result task directory containing capture.json and runtime_trace.json.",
    )
    target.add_argument(
        "--capture",
        type=Path,
        metavar="PATH",
        help="Capture archive directory (the captures/<uuid> folder).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON instead of human text.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="How many tools to list by total time (default: 10).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    result_dir: Path | None = None
    capture_dir: Path | None = None
    trace_path: Path | None = None
    notes: list[str] = []

    if args.capture is not None:
        capture_dir = args.capture.expanduser()
        if not capture_dir.is_dir():
            notes.append(f"Capture path is not a directory: {capture_dir}")
            capture_dir = None
    else:
        result_dir = args.result_dir.expanduser()
        capture_pointer = result_dir / "capture.json"
        trace_path = result_dir / "runtime_trace.json"
        pointer = read_json(capture_pointer)
        if not isinstance(pointer, dict):
            notes.append(f"capture.json missing or unreadable: {capture_pointer}")
        else:
            raw = pointer.get("archive_path") or pointer.get("capture_path")
            if isinstance(raw, str) and raw:
                capture_dir = resolve_capture_dir(raw, result_dir)
                if capture_dir is None:
                    notes.append(f"Capture archive could not be resolved from: {raw}")
            else:
                notes.append("capture.json carries no archive_path or capture_path.")

    report = build_report(
        capture_dir=capture_dir,
        trace_path=trace_path,
        result_dir=result_dir,
        top=max(args.top, 0),
    )
    if notes:
        report["notes"] = list(notes) + list(report["notes"])

    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=False, default=str))
    else:
        print(render_text(report, max(args.top, 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
