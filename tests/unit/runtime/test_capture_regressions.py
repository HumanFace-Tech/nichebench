"""Security, fidelity, and fail-stop regressions using only local stubs."""

from __future__ import annotations

import asyncio
import gzip
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nichebench.execution.runtime.artifacts.redaction import redact_artifact_payload
from nichebench.execution.runtime.capture.errors import CaptureQuotaExceeded, CaptureWriteError
from nichebench.execution.runtime.capture.proxy import ProxyConfig, start_capture_proxy
from nichebench.execution.runtime.capture.scrub import REDACTED, StreamingCredentialScrubber
from nichebench.execution.runtime.capture.session_db import decode_column, export_tables
from nichebench.providers.litellm_client import LiteLLMClient, _current_app_capture, app_capture_scope
from tests.unit.runtime.capture_helpers import events, events_of, make_session, write_opencode_db
from tests.unit.runtime.test_capture_cage_integration import TestCaptureExitPaths as CageFixtures
from tests.unit.runtime.test_capture_cage_integration import _capture_env, _fake_docker, _run_cage
from tests.unit.runtime.test_capture_proxy import _json_response, _post, _Upstream

SECRET = "known-minimax-secret-0123456789"


def assert_no_secret(root: Path, secret: str = SECRET) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes(), path


def test_shared_artifact_redactor_preserves_metrics_not_credentials():
    payload = {
        "token": "credential",
        "client_secret": "secret",
        "signing_secret": "secret",
        "password": 1234,
        "usage": {"input_tokens": 8, "output_tokens": 9},
        "max_tokens": 512,
        "token_count": 12,
        "ordinary": "token: settings.get('token'); api_key=settings.get('api_key')",
        "env": "MINIMAX_API_KEY=unknown-credential",
        "json": '{"authorization":"Bearer unknown-credential","max_tokens":512}',
    }
    clean = redact_artifact_payload(payload)
    assert clean["token"] == clean["client_secret"] == clean["signing_secret"] == REDACTED
    assert clean["password"] == REDACTED
    assert clean["usage"] == payload["usage"]
    assert clean["max_tokens"] == 512 and clean["token_count"] == 12
    assert clean["ordinary"] == payload["ordinary"]
    assert "unknown-credential" not in json.dumps(clean)
    assert json.loads(clean["json"])["max_tokens"] == 512


@pytest.mark.parametrize("cut", range(len(SECRET) + 1))
def test_stream_scrubber_catches_every_credential_split(cut):
    scrubber = StreamingCredentialScrubber([SECRET])
    data = b"prefix:" + SECRET.encode() + b":ordinary token_count=7"
    split = len(b"prefix:") + cut
    clean = scrubber.feed(data[:split]) + scrubber.feed(data[split:]) + scrubber.flush()
    assert clean == b"prefix:[REDACTED]:ordinary token_count=7"
    assert scrubber.redacted


def test_spool_scrubs_split_credentials_and_labels_its_stored_copy(tmp_path):
    session = make_session(tmp_path, known_credentials=[SECRET])
    attempt = session.open_attempt("initial")
    spool = session.spool(attempt, "stdout")
    spool.write(b"ordinary " + SECRET[:10].encode())
    spool.write(SECRET[10:].encode() + b" output")
    spool.close()
    assert attempt.stdout_path.read_bytes() == b"ordinary [REDACTED] output"
    closed = events_of(events(attempt.path), "stream_closed")[0]
    assert closed["exact"] is False and closed["exclusions"]
    assert_no_secret(session.root)
    session.finalise(None)


def test_application_scopes_are_thread_local_and_nested_calls_restore_lanes(tmp_path):
    sessions = [make_session(tmp_path / str(i)) for i in range(2)]
    barrier = threading.Barrier(2)
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    def worker(index):
        session = sessions[index]
        assert _current_app_capture() is None
        with app_capture_scope(session, label=f"outer-{index}"):
            barrier.wait(timeout=5)
            with app_capture_scope(session, lane="review", label=f"inner-{index}"):
                assert _current_app_capture() is session
                LiteLLMClient().generate("inner", model="openai/offline")
            assert session.current_label == f"outer-{index}"
            barrier.wait(timeout=5)
            LiteLLMClient().generate("outer", model="openai/offline")
        assert _current_app_capture() is None

    with patch("nichebench.providers.litellm_client.LITELLM_MODULE") as module:
        module.completion.return_value = response
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, index) for index in range(2)]
            for future in futures:
                future.result(timeout=10)
    assert _current_app_capture() is None
    for index, session in enumerate(sessions):
        calls = events_of(events(session.run.judges[0].path), "application_request")
        assert [r["label"] for r in calls] == [f"inner-{index}", f"outer-{index}"]
        session.finalise(None)


def test_proxy_origin_drops_upstream_v1_and_preserves_encoded_tail(tmp_path):
    observed = []

    def handler(request, body):
        observed.append(request.raw_path)
        return _json_response(200, b"ok")

    upstream = _Upstream(handler)
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    handle = start_capture_proxy(
        session.journal_for(attempt),
        ProxyConfig(
            upstream=upstream.start() + "/v1",
            bind_host="127.0.0.1",
            advertise_host="127.0.0.1",
            allow_insecure_upstream=True,
        ),
    )
    try:
        tail = "/v1/chat%2Fcompletions?q=a%2Fb%20c&plus=%2B&literal=+"
        assert _post(handle, tail, b"{}")[0] == 200
        assert observed == [tail]
        records = events(attempt.path)
        request = events_of(records, "http_request")[0]
        blobs = [r for r in records if r.get("payload_name") == "request_body"]
        assert blobs[0]["request_id"] == str(request["seq"])
    finally:
        handle.stop()
        upstream.stop()
        session.finalise(None)


@pytest.mark.parametrize("compressed", [False, True])
def test_proxy_scrubs_stored_body_not_forwarded_sse_or_gzip(tmp_path, compressed):
    raw = b'data: {"text":"' + SECRET.encode() + b'"}\n\ndata: [DONE]\n\n'
    wire = gzip.compress(raw) if compressed else raw

    async def handler(request, body):
        from aiohttp import web

        assert body == SECRET.encode()
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                **({"Content-Encoding": "gzip"} if compressed else {}),
            }
        )
        await response.prepare(request)
        cut = len(wire) // 2 if compressed else raw.index(SECRET.encode()) + 10
        await response.write(wire[:cut])
        await asyncio.sleep(0.05)
        await response.write(wire[cut:])
        await response.write_eof()
        return response

    upstream = _Upstream(handler)
    session = make_session(tmp_path, known_credentials=[SECRET])
    attempt = session.open_attempt("initial")
    journal = session.journal_for(attempt)
    handle = start_capture_proxy(
        journal,
        ProxyConfig(
            upstream=upstream.start() + "/v1",
            bind_host="127.0.0.1",
            advertise_host="127.0.0.1",
            allow_insecure_upstream=True,
        ),
        upstream_auth=SECRET,
    )
    try:
        assert _post(handle, "/v1/chat/completions", SECRET.encode()) == (200, wire)
        records = events(attempt.path)
        request = next(r for r in records if r.get("payload_name") == "request_body")
        assert journal.blobs.get(request["payload_ref"]) == b"[REDACTED]"
        assert request["exact"] is False
        responses = [r for r in records if r.get("payload_name") == "response_body"]
        if compressed:
            assert not responses
            assert events_of(records, "response_body_withheld")[0]["exact"] is False
        else:
            clean = b"".join(journal.blobs.get(r["payload_ref"]) for r in responses)
            assert clean == raw.replace(SECRET.encode(), b"[REDACTED]")
            assert events_of(records, "http_response_complete")[0]["exact"] is False
        assert_no_secret(session.root)
    finally:
        handle.stop()
        upstream.stop()
        session.finalise(None)
    if compressed:
        assert session.descriptor()["completeness"] == "incomplete"


def test_db_blob_export_is_lossless_and_row_identity_is_json_safe(tmp_path):
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    db = tmp_path / "blob.db"
    raw = b"\x00\xff\x80arbitrary payload"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE binary_data (id BLOB PRIMARY KEY, data BLOB, n INTEGER)")
        conn.execute("INSERT INTO binary_data VALUES (?, ?, ?)", (b"\xffid", raw, 2**60))
    journal = session.journal_for(attempt)
    summary = export_tables(db, journal)
    assert not summary.get("error")
    row = events_of(events(attempt.path), "db_row")[0]
    payload = json.loads(journal.blobs.get(row["payload_ref"]))
    assert decode_column(payload["data"]) == raw
    assert decode_column(payload["n"]) == 2**60
    assert decode_column(row["row_identity"]["id"]) == b"\xffid"
    session.finalise(None)


def test_db_checkpoints_scrub_raw_pages_and_keep_running_tool_inputs(tmp_path):
    session = make_session(tmp_path, known_credentials=[SECRET])
    attempt = session.open_attempt("initial")
    data_dir = tmp_path / "live"
    db = write_opencode_db(data_dir, sessions=3, messages=51, parts=1)
    tool = {
        "type": "tool",
        "tool": "write",
        "state": {
            "status": "running",
            "input": {"content": "x" * 90000, "secret_echo": SECRET},
        },
    }
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE part SET data = ? WHERE id = ?", (json.dumps(tool), "ses_000_msg_000_part_000"))
        conn.execute("CREATE TABLE credentials (id TEXT PRIMARY KEY, value BLOB) WITHOUT ROWID")
        conn.execute("INSERT INTO credentials VALUES (?, ?)", ("auth", SECRET.encode()))
    session.set_context(cage_xdg_data_host=data_dir)
    assert session.checkpoint_session(attempt, min_interval_seconds=0)
    assert_no_secret(session.root)
    with sqlite3.connect(attempt.db_snapshot_path / "opencode.db") as conn:
        row = conn.execute("SELECT data FROM part WHERE id = ?", ("ses_000_msg_000_part_000",)).fetchone()
        copied = json.loads(row[0])
        assert copied["state"]["status"] == "running"
        assert len(copied["state"]["input"]["content"]) == 90000
        assert copied["state"]["input"]["secret_echo"] == REDACTED
    summary = session.archive_session(attempt, data_dir)
    assert summary["export"]["tables"]["message"]["rows_exported"] == 153
    assert summary["export"]["tables"]["session"]["rows_exported"] == 3
    assert_no_secret(session.root)
    # Read through SQLite rather than the main file: commits may still be in WAL.
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT value FROM credentials WHERE id = 'auth'").fetchone()[0] == SECRET.encode()
    session.finalise(None)


def test_native_tool_envelope_counts_calls_results_and_retains_bad_lines(tmp_path):
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    part = {
        "id": "part-1",
        "messageID": "msg-1",
        "sessionID": "session-1",
        "type": "tool",
        "tool": "write",
        "callID": "call-1",
        "state": {
            "status": "completed",
            "input": {"content": "x" * 90000},
            "output": "written",
        },
    }
    native = {"type": "tool_use", "timestamp": 12345, "part": part}
    lines = [
        json.dumps(native),
        "{malformed",
        "[1,2]",
        json.dumps({"type": "text", "part": {"type": "text", "text": "final"}}),
    ]
    summary = session.record_agent_stream(session.journal_for(attempt), "\n".join(lines), "final")
    assert summary["tool_calls"] == summary["tool_results"] == 1
    assert summary["assistant_text"] == "final"
    assert summary["unparsed_lines"] == 2 and not summary["parse_complete"]
    assert session.descriptor()["completeness"] == "incomplete"
    journal = session.journal_for(attempt)
    records = events(attempt.path)
    call = events_of(records, "agent_tool_call")[0]
    assert call["part_id"] == "part-1" and call["message_id"] == "msg-1"
    assert len(call["input"]["content"]) == 90000
    refs = [r for r in records if r.get("payload_name") == "agent_event"]
    assert journal.blobs.get(refs[0]["payload_ref"]) == lines[0].encode()
    session.finalise(None)


def test_capture_cage_returns_only_assistant_text_even_when_it_is_empty(tmp_path):
    raw = "\n".join(
        json.dumps(part)
        for part in [
            {"type": "reasoning", "part": {"type": "reasoning", "text": "secret thoughts"}},
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "input": {"command": "true"},
                        "output": "tool title",
                        "status": "completed",
                    },
                },
            },
        ]
    )
    fixture = tmp_path / "events.jsonl"
    fixture.write_text(raw)
    fake_bin = _fake_docker(tmp_path, f"/bin/cat {fixture}")
    executor = CageFixtures()._executor(tmp_path)
    session = make_session(tmp_path / "results")
    with _capture_env(fake_bin, executor, tmp_path):
        result = _run_cage(executor, tmp_path, session)
    assert result[0] == ""
    assert "secret thoughts" in result[2]  # Trace, not judge output.
    from nichebench.execution.runtime.executor.review_nudge import detect_meaningful_first_pass

    assert not detect_meaningful_first_pass(result[0], None, result[2])


def test_capture_final_text_matches_legacy_prose_and_review_gate(tmp_path):
    legacy = "Completed the offline task."
    native = {"type": "text", "part": {"type": "text", "text": legacy}}
    fixture = tmp_path / "events.jsonl"
    fixture.write_text(json.dumps(native))
    fake_bin = _fake_docker(tmp_path, f"/bin/cat {fixture}")
    executor = CageFixtures()._executor(tmp_path)
    session = make_session(tmp_path / "results")
    with _capture_env(fake_bin, executor, tmp_path):
        result = _run_cage(executor, tmp_path, session)
    assert result[0] == legacy
    from nichebench.execution.runtime.executor.review_nudge import detect_meaningful_first_pass

    assert detect_meaningful_first_pass(result[0], None, result[2])
    records = events(session.run.attempts[0].path)
    assert events_of(records, "agent_stream_summary")[0]["scoring_text_matches_stream"]


def test_capture_quota_fail_stops_live_child_and_writes_descriptor(tmp_path):
    fake_bin = _fake_docker(tmp_path, "printf '%010000d' 0\nsleep 30")
    executor = CageFixtures()._executor(tmp_path, runtime_watchdog_enable=False)
    session = make_session(tmp_path / "results", quota_bytes=4096)
    with _capture_env(fake_bin, executor, tmp_path), pytest.raises(RuntimeError, match="capture storage failed"):
        _run_cage(executor, tmp_path, session, timeout_seconds=20)
    assert session.capture_stopped
    descriptor = json.loads((session.root / "capture.json").read_text())
    assert descriptor["completeness"] == "incomplete"
    assert any("capture" in reason.lower() for reason in descriptor["reasons"])
    assert not Path(session.context["cage_state_cleanup"]).exists()


def test_capture_startup_disk_failure_still_records_incomplete_descriptor(tmp_path):
    session = make_session(tmp_path)
    with patch(
        "nichebench.execution.runtime.capture.store.os.posix_fallocate", side_effect=OSError("full")
    ), pytest.raises(CaptureWriteError):
        session.open_attempt("initial")
    assert json.loads((session.root / "capture.json").read_text())["completeness"] == "incomplete"
    assert not list(session.root.rglob(".emergency-space"))


def test_sqlite_disk_full_is_a_storage_failure_not_a_missing_snapshot(tmp_path):
    from nichebench.execution.runtime.capture.session_db import _backup_to

    source, target = MagicMock(), MagicMock()
    error = sqlite3.OperationalError("disk full")
    error.sqlite_errorcode = getattr(sqlite3, "SQLITE_FULL")
    source.backup.side_effect = error
    with patch(
        "nichebench.execution.runtime.capture.session_db.sqlite3.connect", side_effect=[source, target]
    ), pytest.raises(CaptureWriteError, match="SQLite backup storage failed"):
        _backup_to(tmp_path / "live.db", tmp_path / "copy.db")
    source.close.assert_called_once()
    target.close.assert_called_once()


def test_midstream_proxy_quota_notifies_session_and_refuses_retries(tmp_path):
    async def handler(request, body):
        from aiohttp import web

        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"first chunk")
        await asyncio.sleep(0.1)
        await response.write(b"second chunk")
        await response.write_eof()
        return response

    upstream = _Upstream(handler)
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    journal = session.journal_for(attempt)
    handle = start_capture_proxy(
        journal,
        ProxyConfig(
            upstream=upstream.start(),
            bind_host="127.0.0.1",
            advertise_host="127.0.0.1",
            allow_insecure_upstream=True,
        ),
    )
    original = journal.store_payload
    chunks = []

    def fail_second(name, data, **kwargs):
        if name == "response_body":
            chunks.append(data)
            if len(chunks) == 2:
                raise CaptureQuotaExceeded("test quota")
        return original(name, data, **kwargs)

    try:
        with patch.object(journal, "store_payload", side_effect=fail_second):
            assert _post(handle, "/v1/chat/completions", b"{}")[1] == b"first chunk"
        assert session.capture_stopped
        assert _post(handle, "/v1/chat/completions", b"{}")[0] == 507
        assert len(upstream.requests) == 1
        assert events_of(events(attempt.path), "http_response_complete")[0]["state"] == "partial"
        assert events_of(events(attempt.path), "capture_storage_stopped")
    finally:
        handle.stop()
        upstream.stop()
        session.finalise(None)


def test_later_attempts_and_judge_attempts_never_overwrite_closed_attempts(tmp_path):
    session = make_session(tmp_path)
    first = session.open_attempt("initial")
    session.journal_for(first).append({"event": "initial_marker"})
    session.finalise(first)
    original = first.events_path.read_bytes()
    second = session.open_attempt("review_nudge")
    session.journal_for(second).append({"event": "review_marker"})
    session.finalise(second)
    assert first.events_path.read_bytes() == original
    assert events_of(events(second.path), "capture_attempt_finalised")
    a = session.open_judge_attempt("first")
    b = session.open_judge_attempt("second")
    assert a.index == 1 and b.index == 2 and a.path != b.path
    session.finalise(None)


def test_observed_a_b_c_compaction_order_has_individual_native_payload_refs(tmp_path):
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    lines = [json.dumps({"type": "text", "part": {"type": "text", "id": c, "text": c}}) for c in "ABC"]
    lines.append(json.dumps({"type": "compaction", "part": {"type": "compaction", "auto": True}}))
    summary = session.record_agent_stream(session.journal_for(attempt), "\n".join(lines), "A\nB\nC")
    assert summary["assistant_text"] == "A\nB\nC"
    native = [r for r in events(attempt.path) if r.get("payload_name") == "agent_event"]
    journal = session.journal_for(attempt)
    assert [journal.blobs.get(r["payload_ref"]).decode() for r in native] == lines
    assert all("history" not in r and "messages" not in r for r in native)
    session.finalise(None)
