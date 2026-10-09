"""Agent stream parsing, scoring-text parity, and session-store archive tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from nichebench.execution.runtime.capture.agent_stream import derive_scoring_text, parse_opencode_json_stream
from nichebench.execution.runtime.capture.session_db import (
    archive_session_state,
    consistent_backup,
    export_tables,
    normalize_agent_timeline,
)
from tests.unit.runtime.capture_helpers import close_open_databases, events, events_of, make_session, write_opencode_db


class _Collector:
    """Collect emitted journal records for assertions."""

    def __init__(self) -> None:
        self.records: list = []

    def __call__(self, record) -> None:
        self.records.append(record)

    @property
    def kinds(self) -> list:
        return [record["event"] for record in self.records]

    def first(self, event: str) -> dict:
        return next(record for record in self.records if record["event"] == event)


class TestAgentStreamParsing:
    def test_successful_text_and_tool_parts_are_recorded(self):
        lines = [
            json.dumps(
                {
                    "type": "message.part",
                    "sessionID": "ses_1",
                    "messageID": "msg_1",
                    "part": {"id": "prt_1", "type": "text", "text": "Reading the module"},
                }
            ),
            json.dumps(
                {
                    "type": "message.part",
                    "sessionID": "ses_1",
                    "messageID": "msg_1",
                    "part": {"id": "prt_2", "type": "tool", "tool": "read", "input": {"filePath": "/a/b.php"}},
                }
            ),
            json.dumps(
                {
                    "type": "message.part",
                    "sessionID": "ses_1",
                    "messageID": "msg_1",
                    "part": {"id": "prt_3", "type": "tool", "tool": "read", "output": "file body"},
                }
            ),
            json.dumps(
                {
                    "type": "message.part",
                    "sessionID": "ses_1",
                    "messageID": "msg_2",
                    "part": {"id": "prt_4", "type": "text", "text": "Done"},
                }
            ),
        ]
        collector = _Collector()
        summary = parse_opencode_json_stream(lines, collector)
        assert summary["assistant_text"] == "Reading the module\nDone"
        assert collector.kinds[:4] == ["agent_text", "agent_tool_call", "agent_tool_result", "agent_text"]
        # The derived scoring text is emitted once at the end, never per part.
        assert collector.kinds[-1] == "assistant_completed_text"

    def test_full_large_write_payload_is_captured_uncapped(self):
        big = "L" * 300_000
        line = json.dumps(
            {
                "messageID": "msg_1",
                "role": "assistant",
                "part": {
                    "id": "prt_1",
                    "type": "tool",
                    "tool": "write",
                    "input": {"filePath": "/a.php", "content": big},
                },
            }
        )
        collector = _Collector()
        parse_opencode_json_stream([line], collector)
        call = collector.first("agent_tool_call")
        assert len(call["input"]["content"]) == 300_000

    def test_reasoning_parts_are_recorded_separately_from_text(self):
        lines = [
            json.dumps(
                {
                    "messageID": "msg_1",
                    "role": "assistant",
                    "part": {"id": "prt_1", "type": "reasoning", "text": "think about the schema"},
                }
            ),
            json.dumps(
                {
                    "messageID": "msg_1",
                    "role": "assistant",
                    "part": {"id": "prt_2", "type": "text", "text": "Final answer"},
                }
            ),
        ]
        collector = _Collector()
        summary = parse_opencode_json_stream(lines, collector)
        assert summary["reasoning_parts"] == 1
        assert summary["assistant_text"] == "Final answer"
        assert collector.first("agent_reasoning")["reasoning"] == "think about the schema"

    def test_step_and_error_parts_are_recorded(self):
        lines = [
            json.dumps({"part": {"type": "step-start"}}),
            json.dumps({"part": {"type": "step-finish"}}),
            json.dumps({"part": {"type": "error", "error": "tool blew up"}}),
        ]
        collector = _Collector()
        summary = parse_opencode_json_stream(lines, collector)
        kinds = collector.kinds
        assert "agent_step_start" in kinds
        assert "agent_step_finish" in kinds
        assert summary["errors"] == ["tool blew up"]

    def test_malformed_lines_do_not_break_parsing(self):
        lines = ["not json at all", "", json.dumps({"part": {"type": "text", "text": "ok"}})]
        collector = _Collector()
        summary = parse_opencode_json_stream(lines, collector)
        assert summary["assistant_text"] == "ok"

    def test_nested_parts_array_form_is_supported(self):
        line = json.dumps(
            {
                "role": "assistant",
                "messageID": "msg_9",
                "parts": [
                    {"id": "p1", "type": "text", "text": "first"},
                    {"id": "p2", "type": "text", "text": "second"},
                ],
            }
        )
        collector = _Collector()
        summary = parse_opencode_json_stream([line], collector)
        assert summary["assistant_text"] == "first\nsecond"


class TestScoringTextParity:
    def test_scoring_text_excludes_reasoning_and_tool_json(self):
        parts = [
            {"type": "reasoning", "text": "internal chain of thought"},
            {"type": "tool", "tool": "bash", "input": {"command": "ls"}},
            {"type": "text", "text": "Here is the summary"},
        ]
        assert derive_scoring_text(parts) == "Here is the summary"

    def test_scoring_text_matches_agent_stdout_text(self):
        """The stream-derived text equals the plain text the cage returns."""
        stream_lines = [
            json.dumps({"role": "assistant", "part": {"type": "reasoning", "text": "hidden"}}),
            json.dumps({"role": "assistant", "part": {"type": "text", "text": "I updated the module."}}),
        ]
        collector = _Collector()
        summary = parse_opencode_json_stream(stream_lines, collector)
        # What the legacy stdout path produces for the same run.
        legacy_stdout_text = "I updated the module."
        assert summary["assistant_text"].strip() == legacy_stdout_text.strip()

    def test_session_records_scoring_text_parity_against_stream(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        stdout = "\n".join(
            [
                json.dumps({"role": "assistant", "part": {"type": "reasoning", "text": "hidden"}}),
                json.dumps({"role": "assistant", "part": {"type": "text", "text": "final answer"}}),
            ]
        )
        summary = session.record_agent_stream(journal, stdout, "final answer")
        assert summary["assistant_text"] == "final answer"
        recorded = events_of(events(attempt.path), "agent_stream_summary")[0]
        assert recorded["scoring_text_matches_stream"] is True

    def test_journal_records_the_derived_scoring_text_once(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        stdout = json.dumps({"role": "assistant", "part": {"type": "text", "text": "final answer"}})
        session.record_agent_stream(journal, stdout, "final answer")
        completed = events_of(events(attempt.path), "assistant_completed_text")
        assert len(completed) == 1
        assert completed[0]["text"] == "final answer"


class TestSessionStoreArchive:
    def test_consistent_backup_includes_wal_committed_rows(self, tmp_path):
        data_dir = tmp_path / "xdg-data"
        db_path = write_opencode_db(data_dir, sessions=1, messages=2, parts=1, wal_only_row=True, keep_open=True)
        try:
            assert (db_path.parent / "opencode.db-wal").exists()
            dest = tmp_path / "backup"
            backup = consistent_backup(db_path, dest)
            assert backup is not None and backup.exists()

            conn = sqlite3.connect(backup)
            rows = conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
            wal_row = conn.execute("SELECT id FROM message WHERE id = 'ses_000_msg_wal'").fetchone()
            conn.close()
            # The row written after the main commit lives only in the WAL; a naive
            # file copy of opencode.db would have missed it.
            assert rows == 3
            assert wal_row is not None
        finally:
            close_open_databases()

    def test_export_covers_all_sessions_and_rows(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        data_dir = tmp_path / "xdg-data"
        write_opencode_db(data_dir, sessions=3, messages=4, parts=2)

        summary = archive_session_state(data_dir, attempt.path, journal)
        assert summary["export"]["tables"]["session"]["rows_exported"] == 3
        assert summary["export"]["tables"]["message"]["rows_exported"] == 12
        assert summary["export"]["tables"]["part"]["rows_exported"] == 24

        row_events = events_of(events(attempt.path), "db_row")
        assert len(row_events) == 39
        assert all(record["boundary"] == "db_snapshot" for record in row_events)

    def test_exported_rows_preserve_raw_json_and_ids(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        data_dir = tmp_path / "xdg-data"
        write_opencode_db(data_dir, sessions=1, messages=1, parts=1)
        archive_session_state(data_dir, attempt.path, journal)

        row_events = events_of(events(attempt.path), "db_row")
        message_rows = [r for r in row_events if r["table"] == "message"]
        payload = json.loads(journal.blobs.get(message_rows[0]["payload_ref"]))
        from nichebench.execution.runtime.capture.session_db import decode_column

        payload = {key: decode_column(value) for key, value in payload.items()}
        assert payload["id"] == "ses_000_msg_000"
        assert payload["data"] == json.dumps(
            {"role": "user", "finish": "stop", "usage": {"input_tokens": 10, "output_tokens": 5}}
        )
        assert payload["session_id"] == "ses_000"

    def test_more_than_one_hundred_messages_are_all_exported(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        data_dir = tmp_path / "xdg-data"
        write_opencode_db(data_dir, sessions=2, messages=80, parts=1)
        summary = archive_session_state(data_dir, attempt.path, journal)
        assert summary["export"]["tables"]["message"]["rows_exported"] == 160

    def test_timeline_ordering_is_explicit_and_chronological(self, tmp_path):
        data_dir = tmp_path / "xdg-data"
        db_path = write_opencode_db(data_dir, sessions=2, messages=3, parts=2)
        timeline = normalize_agent_timeline(db_path)
        assert len(timeline) == 12
        times = [record["message_time"] for record in timeline]
        assert times == sorted(times)
        assert all(record["ordering_basis"] for record in timeline)
        assert timeline[0]["part_type"] == "text"

    def test_timeline_does_not_claim_global_order(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        data_dir = tmp_path / "xdg-data"
        write_opencode_db(data_dir, sessions=1, messages=2, parts=1)
        archive_session_state(data_dir, attempt.path, journal)
        exported = events_of(events(attempt.path), "agent_timeline_exported")
        assert exported[0]["ordering_basis"].startswith("message.time_created")

    def test_missing_db_is_recorded_not_raised(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        summary = archive_session_state(tmp_path / "nonexistent", attempt.path, journal)
        assert summary["error"] == "db_missing"
        assert events_of(events(attempt.path), "db_snapshot_skipped")

    def test_export_reports_row_cap_for_tests(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        data_dir = tmp_path / "xdg-data"
        write_opencode_db(data_dir, sessions=1, messages=10, parts=1)
        summary = export_tables(data_dir / "opencode" / "opencode.db", journal, max_rows_per_table=3)
        assert summary["tables"]["message"]["rows_total"] == 10
        assert summary["tables"]["message"]["rows_exported"] == 3

    def test_unsent_versus_sent_tool_output_is_documented(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        data_dir = tmp_path / "xdg-data"
        write_opencode_db(data_dir)
        summary = archive_session_state(data_dir, attempt.path, journal)
        assert "never transmitted upstream" in summary["unsent_vs_sent"]

    def test_archived_db_is_a_copy_not_the_live_file(self, tmp_path):
        session = make_session(tmp_path)
        attempt = session.open_attempt("initial")
        journal = session.journal_for(attempt)
        data_dir = tmp_path / "xdg-data"
        db_path = write_opencode_db(data_dir)
        archive_session_state(data_dir, attempt.path, journal)
        archived = attempt.db_snapshot_path / "opencode.db"
        assert archived.exists()
        assert archived.resolve() != db_path.resolve()
        assert Path(session.root).resolve() != Path(data_dir).resolve()
