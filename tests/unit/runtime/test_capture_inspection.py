"""Transport fidelity and final pointer snapshots, without provider calls."""

from __future__ import annotations

import asyncio
import http.client
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nichebench.execution.runtime.capture.inspection import (
    read_payload,
    reconstruct_response,
    split_sse_frames,
    summarize_sse,
    summarize_transport,
)
from nichebench.execution.runtime.capture.proxy import ProxyConfig, start_capture_proxy
from nichebench.providers.litellm_client import _current_app_capture
from tests.unit.runtime.capture_helpers import events, events_of, make_session
from tests.unit.runtime.test_capture_flow import _capture_config, _make_executor, _runtime_case
from tests.unit.runtime.test_capture_proxy import _json_response, _post, _Upstream


def store_response(session, attempt, pieces, state="complete", request_id="1"):
    journal = session.journal_for(attempt)
    offset = 0
    for piece in pieces:
        journal.store_payload("response_body", piece, extra={"offset": offset, "request_id": request_id})
        offset += len(piece)
    journal.append(
        {
            "event": "http_response_complete",
            "state": state,
            "request_id": request_id,
            "exact": state == "complete",
            "bytes_received": offset,
            "bytes_forwarded": offset,
            "stored_byte_length": offset,
        }
    )


def test_response_reconstruction_joins_chunks_once_before_json_parsing(tmp_path):
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    body = b'data: {"choices":[{"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    store_response(session, attempt, [body[:12], body[12:43], body[43:]])
    records = events(attempt.path)
    # A non-body reference to the same blob must not be counted as another chunk.
    records.append({"event": "other_reference", "request_id": "1", "payload_ref": records[0]["payload_ref"]})
    response = reconstruct_response(attempt.path, list(reversed(records)), "1")
    assert response.body == body and response.exact and not response.problems
    summary = summarize_sse(response.body)
    assert summary["json_frames"] == summary["done_frames"] == summary["finish_reason_frames"] == 1
    assert summary["unparsed_complete_frames"] == summary["partial_frames"] == 0
    session.finalise(None)


@pytest.mark.parametrize(
    "body,complete,partial",
    [
        (b'data: {"x":1}\n', 0, 1),
        (b'data: {"x":1}\n\n:comment\n', 1, 0),
        (b'data: {\r\ndata: "x":1}\r\n\r\n', 1, 0),
        (b'data: {"x":1}\n\ndata: {"x":', 1, 1),
    ],
)
def test_sse_fragments_are_not_malformed_complete_json_events(body, complete, partial):
    frames = split_sse_frames(body)
    assert sum(frame.terminated for frame in frames) == complete
    assert sum(not frame.terminated for frame in frames) == partial
    assert summarize_sse(body)["unparsed_complete_frames"] == 0


def test_sse_provider_error_counts_use_complete_frames_not_unfinished_fragments():
    body = b'data: {"error":{"type":"offline_error"}}\n\ndata: {"error":'
    summary = summarize_sse(body)
    assert summary["error_frames"] == 1 and summary["partial_frames"] == 1
    assert summary["unparsed_complete_frames"] == 0


@pytest.mark.parametrize("offset", [0, 20])
def test_response_reconstruction_rejects_overlaps_or_missing_bytes(tmp_path, offset):
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    store_response(session, attempt, [b"first", b"second"])
    records = events(attempt.path)
    records[1]["offset"] = offset
    with pytest.raises(ValueError, match="gap or overlap"):
        reconstruct_response(attempt.path, records, "1")
    session.finalise(None)


def test_payload_reader_checks_integrity_and_large_manifests(tmp_path):
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    payload = b"arbitrary binary\xff" * 10000
    stored = session.journal_for(attempt).store_payload("response_body", payload)
    assert read_payload(attempt.path, stored["payload_ref"]) == payload
    ref = dict(stored["payload_ref"], sha256="../../escape")
    with pytest.raises(ValueError, match="invalid capture blob digest"):
        read_payload(attempt.path, ref)
    session.finalise(None)


def test_transport_summary_scopes_request_ids_to_attempts_and_deduplicates_references():
    records = []
    for attempt in [1, 2]:
        base = {"invocation_id": "run", "attempt": attempt}
        records.extend(
            [
                {**base, "event": "http_request", "seq": 1},
                {
                    **base,
                    "event": "http_response_complete",
                    "request_id": "1",
                    "state": "complete",
                    "exact": True,
                    "bytes_received": 5,
                    "bytes_forwarded": 5,
                    "stored_byte_length": 5,
                },
            ]
        )
    summary = summarize_transport(records)
    assert summary["http_attempts"] == summary["response_terminals"] == summary["exact_responses"] == 2
    assert summary["bytes_received"] == 10 and not summary["incomplete_reasons"]


def test_legacy_disconnect_evidence_marks_descriptor_incomplete_without_callback(tmp_path):
    session = make_session(tmp_path)
    attempt = session.open_attempt("initial")
    journal = session.journal_for(attempt)
    start = journal.append({"event": "http_request"})
    store_response(session, attempt, [b'data: {"x":'], state="disconnect", request_id=str(start["seq"]))
    # Simulate the old bug: no mark_incomplete call, then finalise the descriptor.
    session.finalise(None)
    descriptor = json.loads((session.root / "capture.json").read_text())
    assert descriptor["completeness"] == "incomplete"
    assert "transport_stream_incomplete" in descriptor["reasons"]
    assert descriptor["transport_summary"]["response_states"] == {"disconnect": 1}
    response = reconstruct_response(attempt.path, events(attempt.path), str(start["seq"]))
    assert not response.exact and response.problems == ("stream_incomplete",)
    assert summarize_sse(response.body)["partial_frames"] == 1


def test_upstream_disconnect_flushes_credential_prefix_and_propagates_incomplete(tmp_path):
    prefix = b'data: {"x":"value"}\n\nz'

    async def handler(request, body):
        from aiohttp import web

        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(prefix)
        await asyncio.sleep(0.05)
        request.transport.abort()
        return response

    upstream = _Upstream(handler)
    session = make_session(tmp_path, known_credentials=["zzzz-known-secret"])
    attempt = session.open_attempt("initial")
    handle = start_capture_proxy(
        session.journal_for(attempt),
        ProxyConfig(
            upstream=upstream.start(),
            bind_host="127.0.0.1",
            advertise_host="127.0.0.1",
            allow_insecure_upstream=True,
        ),
    )
    try:
        assert _post(handle, "/v1/chat/completions", b"{}")[1] == prefix
        records = events(attempt.path)
        interrupted = events_of(records, "http_response_stream_interrupted")[0]
        assert interrupted["phase"] == "upstream_read"
        assert interrupted["bytes_received"] == interrupted["bytes_forwarded"] == len(prefix)
        response = reconstruct_response(attempt.path, records, interrupted["request_id"])
        assert response.body == prefix and response.problems == ("stream_incomplete",)
    finally:
        handle.stop()
        upstream.stop()
        session.finalise(None)
    assert session.result_reference()["completeness"] == "incomplete"
    assert session.result_reference()["transport_summary"]["observed_bytes_not_retained"] == 0


@pytest.mark.parametrize("failure", [ConnectionResetError, asyncio.CancelledError])
def test_downstream_write_failure_keeps_observed_prefix_and_correct_forwarded_count(tmp_path, failure):
    from aiohttp import web

    prefix = b"ordinaryz"
    upstream = _Upstream(lambda request, body: _json_response(200, prefix))
    session = make_session(tmp_path, known_credentials=["zzzz-known-secret"])
    attempt = session.open_attempt("initial")
    handle = start_capture_proxy(
        session.journal_for(attempt),
        ProxyConfig(
            upstream=upstream.start(),
            bind_host="127.0.0.1",
            advertise_host="127.0.0.1",
            allow_insecure_upstream=True,
        ),
    )
    original = web.StreamResponse.write

    async def fail_proxy_write(response, data):
        if isinstance(response, web.Response):
            return await original(response, data)
        raise failure("offline downstream cancellation")

    try:
        with patch.object(web.StreamResponse, "write", fail_proxy_write):
            try:
                _post(handle, "/v1/chat/completions", b"{}")
            except http.client.HTTPException:
                assert failure is asyncio.CancelledError
        records = events(attempt.path)
        interrupted = events_of(records, "http_response_stream_interrupted")[0]
        assert interrupted["phase"] == "downstream_write"
        assert interrupted["bytes_forwarded"] == 0 and interrupted["bytes_received"] == len(prefix)
        response = reconstruct_response(attempt.path, records, interrupted["request_id"])
        assert response.body == prefix and response.bytes_forwarded == 0
        assert "observed_bytes_not_retained" not in response.problems
    finally:
        handle.stop()
        upstream.stop()
        session.finalise(None)
    assert session.descriptor()["completeness"] == "incomplete"


def test_final_flow_pointers_refresh_judge_attempts_and_use_independent_final_snapshots(tmp_path):
    executor = _make_executor(_capture_config(runtime_enable_review_nudge=False), tmp_path / "results")
    captured = {}

    def fake_agent(**kwargs):
        captured["session"] = kwargs["capture"]
        attempt = kwargs["capture"].open_attempt("initial")
        kwargs["capture"].journal_for(attempt).append({"event": "initial_marker"})
        kwargs["capture"].finalise(attempt)
        captured["early"] = kwargs["capture"].result_reference()
        return ("final prose", "input", "log", {}, "offline-image", None, None)

    def fake_judge(**kwargs):
        _current_app_capture().note_app_call("offline-judge", {"max_tokens": 1})
        return 1.0, {"offline": True}

    with (
        patch.object(executor, "_run_runtime_preflight_host", return_value=[]),
        patch.object(executor, "_run_runtime_preflight_workspace", return_value=[]),
        patch.object(executor, "_inject_task_markdown"),
        patch.object(executor, "_load_runtime_checks", return_value=[]),
        patch.object(executor, "_run_container_runtime_task_with_retry", side_effect=fake_agent),
        patch("nichebench.execution.runtime.executor.stages.run_judge_scoring", side_effect=fake_judge),
        patch("nichebench.execution.orchestrator.RuntimeScorer") as scorer,
    ):
        scorer.return_value.run_deterministic_checks.return_value = []
        scorer.return_value.compute_hybrid_score.return_value = SimpleNamespace(
            deterministic_score=1.0,
            judge_score=1.0,
            final_score=1.0,
            passed=True,
        )
        result = executor.execute_runtime_test(_runtime_case())
    assert not result.error
    assert captured["early"]["judge_attempts"] == []
    assert not any("judge_001" in reason for reason in captured["early"]["reasons"])
    reference = result.runtime_artifacts["capture.json"]
    assert reference["judge_attempts"] == ["judges/attempt_001"]
    assert reference == result.runtime_artifacts["metadata.json"]["runtime_capture"]
    assert reference == result.runtime_artifacts["runtime_trace.json"]["capture"]
    executor._save_runtime_artifacts(result)
    folder = tmp_path / "results/runtime/runtime_capture"
    assert json.loads((folder / "capture.json").read_text()) == reference
    assert json.loads((folder / "metadata.json").read_text())["runtime_capture"] == reference
    assert json.loads((folder / "runtime_trace.json").read_text())["capture"] == reference
    published = json.loads((Path(reference["capture_path"]) / "capture.json").read_text())
    assert [a["path"] for a in published["judge_attempts"]] == reference["judge_attempts"]
    reference["judge_attempts"].clear()
    assert captured["session"].result_reference()["judge_attempts"] == ["judges/attempt_001"]
