"""Capture layout, journal, blob store, and quota tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nichebench.execution.runtime.capture import (
    CaptureQuotaExceeded,
    CaptureRun,
    CaptureSession,
    CaptureSettings,
    CaptureUnsupported,
    read_events,
)
from nichebench.execution.runtime.capture.store import BLOCK_SIZE, BlobRef
from tests.unit.runtime.capture_helpers import events, events_of, make_session


class TestCaptureLayout:
    def test_capture_root_lives_under_results_outdir_outside_workspace(self, tmp_path):
        session = make_session(tmp_path)
        assert session.root.parent == tmp_path / "captures"
        assert session.root.exists()

    def test_root_and_files_are_private(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        session.journal_for(attempt).append({"event": "probe"})
        assert oct(session.root.stat().st_mode)[-3:] == "700"
        assert oct(attempt.path.stat().st_mode)[-3:] == "700"
        assert oct(attempt.events_path.stat().st_mode)[-3:] == "600"

    def test_attempts_are_numbered_and_immutable(self, tmp_path):
        session = make_session(tmp_path)
        first = session.open_attempt("initial")
        second = session.open_attempt("retry:json_parse_failure")
        third = session.open_attempt("review_nudge")
        assert first.path.name == "attempt_001"
        assert second.path.name == "attempt_002"
        assert third.path.name == "attempt_003"

        first_id = first.path
        journal = session.journal_for(first)
        journal.append({"event": "first_attempt_marker"})
        journal.close()

        # Opening a later attempt must not touch the earlier one.
        journal_second = session.journal_for(second)
        journal_second.append({"event": "second_attempt_marker"})
        journal_second.close()
        assert first.path == first_id
        assert [e["event"] for e in events(first.path)] == ["first_attempt_marker"]

    def test_judge_lane_uses_separate_subtree(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_judge_attempt("score_runtime")
        assert attempt.path.parent.name == "judges"
        assert attempt.index == 1

    def test_settings_reject_minimal_retention(self):
        with pytest.raises(CaptureUnsupported, match="minimal"):
            CaptureSettings.from_runtime_config(
                {"runtime_capture_enable": True, "runtime_artifact_retention": "minimal"},
                route="minimax_m3",
            )

    def test_settings_accept_standard_and_full(self):
        for retention in ("standard", "full"):
            settings = CaptureSettings.from_runtime_config(
                {"runtime_capture_enable": True, "runtime_artifact_retention": retention},
                route="minimax_m3",
            )
            assert settings.retention == retention

    def test_unsupported_route_fails_loudly(self, tmp_path):
        with pytest.raises(CaptureUnsupported, match="does not support route"):
            CaptureRun.allocate(
                tmp_path,
                CaptureSettings.from_runtime_config({"runtime_capture_enable": True}, route="qwen_local"),
                "t",
            )

    def test_missing_route_fails_loudly(self, tmp_path):
        with pytest.raises(CaptureUnsupported, match="no capture route"):
            CaptureRun.allocate(tmp_path, CaptureSettings.from_runtime_config({"runtime_capture_enable": True}), "t")

    def test_disabled_settings_do_not_validate_route(self):
        CaptureSettings.from_runtime_config({}).assert_route_supported()


class TestJournal:
    def test_records_carry_identity_fields(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        journal.append(
            {"event": "x"},
            boundary="transport_http_entity",
            source="transport",
            request_id="7",
            part_id="p1",
        )
        records = events(attempt.path)
        assert records[0]["seq"] == 1
        assert records[0]["request_id"] == "7"
        assert records[0]["part_id"] == "p1"
        assert records[0]["source"] == "transport"
        assert records[0]["boundary"] == "transport_http_entity"
        assert records[0]["invocation_id"] == session.run.invocation_id
        assert records[0]["attempt"] == 1

    def test_sequence_is_monotonic_and_ordered(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        for index in range(10):
            journal.append({"event": "tick", "index": index})
        seqs = [r["seq"] for r in events(attempt.path)]
        assert seqs == list(range(1, 11))

    def test_records_do_not_embed_cumulative_history(self, tmp_path):
        """A record is a delta; the timeline is the fold, not the payload."""
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        journal.append({"event": "agent_text", "text": "a"})
        journal.append({"event": "agent_text", "text": "b"})
        records = events(attempt.path)
        assert records[0].get("messages") is None
        assert records[1].get("messages") is None
        assert len(json.dumps(records[1])) < 400

    def test_journal_is_fsynced_per_record(self, tmp_path, monkeypatch):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        calls = []
        import nichebench.execution.runtime.capture.store as store_module

        real_fsync = os.fsync
        monkeypatch.setattr(store_module.os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd))[1])
        journal.append({"event": "one"})
        journal.append({"event": "two"})
        assert len(calls) >= 2

    def test_stream_spool_records_offsets_and_timestamps(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        spool = session.spool(attempt, "stdout")
        spool.write(b"first")
        spool.write(b"second")
        spool.close()
        records = events_of(events(attempt.path), "stream_chunk")
        assert [r["offset"] for r in records] == [0, 5]
        assert [r["byte_length"] for r in records] == [5, 6]
        assert all(r["boundary"] == "process_stream" for r in records)
        assert all(r["timestamp"] for r in records)
        assert attempt.stdout_path.read_bytes() == b"firstsecond"


class TestBlobStore:
    def test_identical_payloads_are_stored_once(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        first = journal.store_payload("request_body", b'{"model":"MiniMax-M3","messages":[]}')
        second = journal.store_payload("request_body", b'{"model":"MiniMax-M3","messages":[]}')
        assert first["payload_ref"] == second["payload_ref"]
        assert len(list(attempt.blobs_path.glob("*.bin"))) == 1

    def test_repeated_prefix_does_not_rewrite_whole_timeline(self, tmp_path):
        """Each request body is stored independently; history is not nested."""
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        for turn in range(5):
            journal.store_payload("request_body", json.dumps({"turn": turn}).encode("utf-8"))
        records = events_of(events(attempt.path), "blob_stored")
        assert len(records) == 5
        for record in records:
            assert "history" not in record
            assert record["payload_name"] == "request_body"

    def test_large_payload_roundtrips_across_blocks(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        payload = ("x" * BLOCK_SIZE) + "tail"
        record = journal.store_payload("request_body", payload.encode("utf-8"))
        assert journal.blobs.get(BlobRef.from_dict(record["payload_ref"])) == payload.encode("utf-8")
        manifest = json.loads(
            next(p for p in attempt.blobs_path.glob("*.bin") if p.stat().st_size < 4096).read_text("utf-8")
        )
        assert manifest["block_size"] == BLOCK_SIZE
        assert len(manifest["blocks"]) >= 2

    def test_binary_payload_roundtrips(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        payload = bytes(range(256)) * 8
        record = journal.store_payload("response_body", payload)
        assert journal.blobs.get(record["payload_ref"]) == payload

    def test_read_events_tolerates_truncated_tail(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        journal.append({"event": "one"})
        journal.close()
        with open(attempt.events_path, "a", encoding="utf-8") as handle:
            handle.write('{"event": "half')
        records = read_events(attempt.path)
        assert records[0]["event"] == "one"
        assert records[1]["event"] == "journal_line_unparsable"


class TestQuota:
    def test_quota_breach_raises_instead_of_truncating(self, tmp_path):
        settings = CaptureSettings(enabled=True, quota_bytes=2048, route="minimax_m3")
        run = CaptureRun.allocate(tmp_path, settings, "t")
        session = CaptureSession(run)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        with pytest.raises(CaptureQuotaExceeded, match="quota exceeded"):
            for index in range(200):
                journal.store_payload("request_body", os.urandom(512))

    def test_quota_breach_marks_capture_incomplete_and_run_continues(self, tmp_path):
        settings = CaptureSettings(enabled=True, quota_bytes=2048, route="minimax_m3")
        run = CaptureRun.allocate(tmp_path, settings, "t")
        session = CaptureSession(run)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        with pytest.raises(CaptureQuotaExceeded):
            for index in range(200):
                journal.store_payload("request_body", os.urandom(512))
        session.mark_incomplete("disk_quota_exceeded")
        descriptor = session.descriptor()
        assert descriptor["completeness"] == "incomplete"
        assert "disk_quota_exceeded" in descriptor["reasons"]

    def test_default_quota_is_four_gigabytes(self):
        assert CaptureSettings.from_runtime_config({"runtime_capture_enable": True}).quota_bytes == 4 * 1024**3


class TestDescriptor:
    def test_descriptor_records_completion_reasons_and_paths(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        session.journal_for(attempt).append({"event": "x"})
        session.set_context(attempt_label="initial")
        path = session.finalise(attempt, "nonzero_exit:1")
        assert path is not None
        descriptor = json.loads(Path(path).read_text(encoding="utf-8"))
        assert descriptor["completeness"] == "complete"
        assert "nonzero_exit:1" in descriptor["reasons"]
        assert descriptor["archive_path"] == str(session.root)
        assert descriptor["attempts"][0]["events"].endswith("events.jsonl")
        assert descriptor["context"]["attempt_label"] == "initial"

    def test_descriptor_states_boundary_claims_and_limits(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        path = session.finalise(attempt, "normal_exit")
        descriptor = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "not_observable" in descriptor["boundaries"]
        assert "hidden provider-internal reasoning" in descriptor["boundaries"]["not_observable"]
        assert "application_response" in descriptor["boundaries"]
        assert "not a wire capture" in descriptor["boundaries"]["application_response"]
        assert "never persisted" in descriptor["credential_policy"]

    def test_archive_survives_after_attempt_close(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        session.journal_for(attempt).append({"event": "kept"})
        session.finalise(attempt, "normal_exit")
        assert events(attempt.path)[0]["event"] == "kept"
