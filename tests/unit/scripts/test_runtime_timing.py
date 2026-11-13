"""Unit tests for scripts/runtime_timing.py.

These tests are fully synthetic: no network, no Docker, no DDEV, and no real
capture archive is read. Every scenario builds its own fixture under ``tmp_path``.

Coverage:
- HTTP request/response pairing by ``request_id``, including incomplete pairs.
- Idle/queue gap computation, with ordered and unordered journal records.
- Tool timings from a synthetic ``opencode.db`` ``part`` table, including a
  malformed ``data`` row and a tool part without timing.
- Stage durations parsed from ``runtime_trace.json``.
- Missing capture / missing trace paths degrade gracefully and exit 0.
- ``--json`` emits the documented schema.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_TIMING_PATH = REPO_ROOT / "scripts" / "runtime_timing.py"


@pytest.fixture(scope="module")
def timing():
    """Import scripts/runtime_timing.py from its absolute path."""
    spec = importlib.util.spec_from_file_location("runtime_timing", RUNTIME_TIMING_PATH)
    assert spec and spec.loader, "runtime_timing.py must exist and be importable"
    module = importlib.util.module_from_spec(spec)
    sys.modules["runtime_timing"] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("runtime_timing", None)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _event(name: str, timestamp: str, request_id: str) -> dict[str, Any]:
    return {"event": name, "timestamp": timestamp, "request_id": request_id, "seq": 1}


def _write_journal(path: Path, records: list[dict[str, Any]], raw_lines: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
        for line in raw_lines or []:
            handle.write(line + "\n")


def _make_capture(root: Path, journal_records: list[dict[str, Any]], raw_lines: list[str] | None = None) -> Path:
    """Create a capture archive with one attempt journal (and an empty judge lane)."""
    capture_dir = root / "captures" / "abc123"
    _write_journal(capture_dir / "attempts" / "attempt_001" / "events.jsonl", journal_records, raw_lines)
    (capture_dir / "judges" / "attempt_001").mkdir(parents=True, exist_ok=True)
    (capture_dir / "judges" / "attempt_001" / "events.jsonl").write_text("", encoding="utf-8")
    return capture_dir


def _make_db(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "create table part ("
            "id text primary key, message_id text not null, session_id text not null, "
            "time_created integer not null, time_updated integer not null, data text not null)"
        )
        for index, data in enumerate(rows):
            connection.execute(
                "insert into part (id, message_id, session_id, time_created, time_updated, data) "
                "values (?, ?, ?, ?, ?, ?)",
                (f"prt_{index}", "msg_1", "ses_1", 0, 0, data),
            )
        connection.commit()
    finally:
        connection.close()


def _tool_data(name: str, start: int | None, end: int | None) -> str:
    state: dict[str, Any] = {"status": "completed"}
    if start is not None or end is not None:
        state["time"] = {"start": start, "end": end}
    return json.dumps({"type": "tool", "tool": name, "state": state})


# ---------------------------------------------------------------------------
# HTTP pairing and gaps
# ---------------------------------------------------------------------------


def test_http_pairing_and_gap_calculation(timing):
    records = [
        _event("http_request_sent", "2026-01-01T00:00:00+00:00", "1"),
        _event("http_response_complete", "2026-01-01T00:00:02+00:00", "1"),
        _event("http_request_sent", "2026-01-01T00:00:03+00:00", "2"),
        _event("http_response_complete", "2026-01-01T00:00:05+00:00", "2"),
        _event("http_request_sent", "2026-01-01T00:00:10+00:00", "3"),
    ]

    analysis = timing.analyze_http_journal(records)
    requests = analysis["requests"]

    assert requests["count"] == 2
    assert requests["total_seconds"] == pytest.approx(4.0)
    assert requests["mean_seconds"] == pytest.approx(2.0)
    assert requests["p50_seconds"] == pytest.approx(2.0)
    assert requests["p95_seconds"] == pytest.approx(2.0)
    assert requests["min_seconds"] == pytest.approx(2.0)
    assert requests["max_seconds"] == pytest.approx(2.0)
    # Request "3" was sent but never completed.
    assert requests["incomplete_pairs"] == 1
    assert requests["incomplete_request_ids"] == ["3"]
    assert requests["orphan_responses"] == 0

    gaps = analysis["gap_summary"]
    assert gaps["count"] == 2
    assert gaps["total_seconds"] == pytest.approx(6.0)
    assert gaps["p50_seconds"] == pytest.approx(1.0)
    assert gaps["p95_seconds"] == pytest.approx(5.0)
    assert gaps["max_seconds"] == pytest.approx(5.0)


def test_http_pairing_is_independent_of_record_order(timing):
    ordered = [
        _event("http_request_sent", "2026-01-01T00:00:00+00:00", "1"),
        _event("http_response_complete", "2026-01-01T00:00:02+00:00", "1"),
        _event("http_request_sent", "2026-01-01T00:00:03+00:00", "2"),
        _event("http_response_complete", "2026-01-01T00:00:05+00:00", "2"),
        _event("http_request_sent", "2026-01-01T00:00:10+00:00", "3"),
    ]
    shuffled = list(reversed(ordered))

    forward = timing.analyze_http_journal(ordered)
    backward = timing.analyze_http_journal(shuffled)

    assert forward["requests"]["count"] == backward["requests"]["count"]
    assert forward["requests"]["total_seconds"] == pytest.approx(backward["requests"]["total_seconds"])
    assert forward["gap_summary"]["count"] == backward["gap_summary"]["count"]
    assert forward["gap_summary"]["total_seconds"] == pytest.approx(backward["gap_summary"]["total_seconds"])


def test_malformed_journal_lines_are_skipped(timing, tmp_path):
    journal = tmp_path / "events.jsonl"
    _write_journal(
        journal,
        [_event("http_request_sent", "2026-01-01T00:00:00+00:00", "1")],
        raw_lines=["not json at all", "{broken"],
    )
    records, malformed = timing.read_journal(journal)

    assert malformed == 2
    assert len(records) == 1


# ---------------------------------------------------------------------------
# Tool timings from the session store snapshot
# ---------------------------------------------------------------------------


def test_tool_db_totals_percentiles_and_top_tools(timing, tmp_path):
    db_path = tmp_path / "opencode-db" / "opencode.db"
    _make_db(
        db_path,
        [
            _tool_data("bash", 1000, 3000),  # 2.0 s
            _tool_data("bash", 0, 1000),  # 1.0 s
            _tool_data("read", 0, 500),  # 0.5 s
            _tool_data("edit", None, None),  # no timing information
            "this-is-not-json",  # malformed row
            json.dumps({"type": "text", "text": "ignored"}),
        ],
    )

    tools = timing.analyze_tool_db(db_path)

    assert tools["available"] is True
    assert tools["count"] == 3
    assert tools["total_seconds"] == pytest.approx(3.5)
    assert tools["mean_seconds"] == pytest.approx(3.5 / 3)
    assert tools["p50_seconds"] == pytest.approx(1.0)
    assert tools["p95_seconds"] == pytest.approx(2.0)
    assert tools["min_seconds"] == pytest.approx(0.5)
    assert tools["max_seconds"] == pytest.approx(2.0)
    assert tools["missing_timing"] == 1
    assert tools["malformed_rows"] == 1

    assert tools["by_tool"]["bash"]["count"] == 2
    assert tools["by_tool"]["bash"]["total_seconds"] == pytest.approx(3.0)
    assert tools["by_tool"]["read"]["count"] == 1
    assert tools["top_tools"][0]["tool"] == "bash"
    assert tools["top_tools"][0]["total_seconds"] == pytest.approx(3.0)


def test_tool_db_missing_is_reported_not_raised(timing, tmp_path):
    tools = timing.analyze_tool_db(tmp_path / "does-not-exist" / "opencode.db")

    assert tools["available"] is False
    assert tools["count"] == 0
    assert tools["total_seconds"] == pytest.approx(0.0)


def test_corrupt_tool_db_is_reported_not_raised(timing, tmp_path, capsys):
    db_path = tmp_path / "opencode-db" / "opencode.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_text("this is not a sqlite database at all", encoding="utf-8")

    tools = timing.analyze_tool_db(db_path)
    assert tools["available"] is False
    assert tools["count"] == 0

    capture_dir = tmp_path / "captures" / "abc123"
    (capture_dir / "attempts" / "attempt_001" / "opencode-db").mkdir(parents=True)
    (capture_dir / "attempts" / "attempt_001" / "opencode-db" / "opencode.db").write_text(
        "not a database", encoding="utf-8"
    )

    exit_code = timing.main(["--capture", str(capture_dir)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "no readable opencode.db snapshot" in out


# ---------------------------------------------------------------------------
# Runtime trace stages
# ---------------------------------------------------------------------------


def test_trace_stage_durations(timing, tmp_path):
    trace_path = tmp_path / "runtime_trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T00:01:00+00:00",
                "stages": [
                    {
                        "stage": "workspace_setup",
                        "status": "passed",
                        "started_at": "2026-01-01T00:00:00+00:00",
                        "ended_at": "2026-01-01T00:00:30+00:00",
                    },
                    {
                        "stage": "agent_execution",
                        "status": "passed",
                        "started_at": "2026-01-01T00:00:30+00:00",
                        "ended_at": "2026-01-01T00:01:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    stages, wall = timing.analyze_trace(trace_path)

    assert wall == pytest.approx(60.0)
    assert [stage["stage"] for stage in stages] == ["workspace_setup", "agent_execution"]
    assert stages[0]["duration_seconds"] == pytest.approx(30.0)
    assert stages[1]["duration_seconds"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def test_missing_capture_and_trace_exits_cleanly(timing, tmp_path, capsys):
    result_dir = tmp_path / "runtime" / "drupal_runtime_999"
    result_dir.mkdir(parents=True)

    exit_code = timing.main(["--result-dir", str(result_dir)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "n/a" in out
    assert "no stage data available" in out
    assert "Capture archive not found" in out


def test_missing_capture_path_exits_cleanly(timing, tmp_path, capsys):
    exit_code = timing.main(["--capture", str(tmp_path / "nope")])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "n/a" in out


def test_result_dir_resolves_capture_relative_to_cwd(timing, tmp_path, monkeypatch, capsys):
    _make_capture(
        tmp_path,
        [
            _event("http_request_sent", "2026-01-01T00:00:00+00:00", "1"),
            _event("http_response_complete", "2026-01-01T00:00:02+00:00", "1"),
        ],
    )
    result_dir = tmp_path / "runtime" / "drupal_runtime_001"
    result_dir.mkdir(parents=True)
    (result_dir / "capture.json").write_text(
        json.dumps({"capture_path": "captures/abc123", "archive_path": "captures/abc123"}),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    exit_code = timing.main(["--result-dir", str(result_dir)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "count             : 1 complete" in out


def test_json_output_schema(timing, tmp_path, capsys):
    capture_dir = _make_capture(
        tmp_path,
        [
            _event("http_request_sent", "2026-01-01T00:00:00+00:00", "1"),
            _event("http_response_complete", "2026-01-01T00:00:02+00:00", "1"),
            _event("http_request_sent", "2026-01-01T00:00:04+00:00", "2"),
        ],
    )
    db_path = capture_dir / "attempts" / "attempt_001" / "opencode-db" / "opencode.db"
    _make_db(db_path, [_tool_data("bash", 0, 1500), _tool_data("read", 0, 500)])

    result_dir = tmp_path / "runtime" / "drupal_runtime_002"
    result_dir.mkdir(parents=True)
    (result_dir / "capture.json").write_text(
        json.dumps({"archive_path": str(capture_dir)}),
        encoding="utf-8",
    )
    (result_dir / "runtime_trace.json").write_text(
        json.dumps(
            {
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T00:01:00+00:00",
                "stages": [
                    {
                        "stage": "agent_execution",
                        "status": "passed",
                        "started_at": "2026-01-01T00:00:00+00:00",
                        "ended_at": "2026-01-01T00:01:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = timing.main(["--result-dir", str(result_dir), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    for key in ("wall_seconds", "model_requests", "tool_calls", "stages", "notes"):
        assert key in payload

    assert payload["wall_seconds"] == pytest.approx(60.0)
    assert payload["model_requests"]["count"] == 1
    assert payload["model_requests"]["incomplete_pairs"] == 1
    assert payload["model_requests"]["idle_or_queue_gap"]["count"] == 1
    assert payload["tool_calls"]["count"] == 2
    assert payload["tool_calls"]["total_seconds"] == pytest.approx(2.0)
    assert payload["stages"][0]["stage"] == "agent_execution"
    assert payload["stages"][0]["duration_seconds"] == pytest.approx(60.0)

    notes = " ".join(payload["notes"])
    assert "round-trip" in notes
    assert "not GPU telemetry" in notes
    assert "idle_or_queue_gap" in notes


def test_top_limits_the_tool_list(timing, tmp_path, capsys):
    capture_dir = _make_capture(tmp_path, [])
    db_path = capture_dir / "attempts" / "attempt_001" / "opencode-db" / "opencode.db"
    _make_db(
        db_path,
        [
            _tool_data("bash", 0, 3000),
            _tool_data("read", 0, 2000),
            _tool_data("edit", 0, 1000),
        ],
    )

    exit_code = timing.main(["--capture", str(capture_dir), "--json", "--top", "1"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert len(payload["tool_calls"]["top_tools"]) == 1
    assert payload["tool_calls"]["top_tools"][0]["tool"] == "bash"


def test_malformed_records_are_counted_in_the_report(timing, tmp_path, capsys):
    capture_dir = _make_capture(
        tmp_path,
        [_event("http_request_sent", "2026-01-01T00:00:00+00:00", "1")],
        raw_lines=["not json", "still not json"],
    )
    db_path = capture_dir / "attempts" / "attempt_001" / "opencode-db" / "opencode.db"
    _make_db(db_path, ["not json either", _tool_data("bash", 0, 1000)])

    exit_code = timing.main(["--capture", str(capture_dir), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["model_requests"]["malformed_lines"] == 2
    assert payload["tool_calls"]["malformed_rows"] == 1
    assert payload["journals"][0]["lane"] == "attempt"
    assert payload["journals"][0]["malformed_lines"] == 2
    assert payload["model_requests"]["count"] == 0


def test_malformed_counts_appear_in_text_output(timing, tmp_path, capsys):
    capture_dir = _make_capture(
        tmp_path,
        [_event("http_request_sent", "2026-01-01T00:00:00+00:00", "1")],
        raw_lines=["not json"],
    )
    db_path = capture_dir / "attempts" / "attempt_001" / "opencode-db" / "opencode.db"
    _make_db(db_path, ["not json either"])

    exit_code = timing.main(["--capture", str(capture_dir)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "1 malformed journal lines" in out
    assert "malformed rows    : 1" in out
