"""Unit tests for runtime trajectory capture helpers in TestExecutor."""

import json
import time
from datetime import datetime, timezone
from unittest.mock import patch

from nichebench.core.executor import TestExecutor

# ---------------------------------------------------------------------------
# Minimal executor factory (avoids loading prompts / config files)
# ---------------------------------------------------------------------------


def _make_executor() -> TestExecutor:
    mut_cfg = {"provider": "openai", "model": "gpt-4o", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-4o", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

    with (
        patch("nichebench.core.executor.get_config") as mock_config,
        patch.object(TestExecutor, "_load_system_prompt", return_value=None),
        patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
    ):
        mock_config.return_value.get_evaluation_config.return_value = {}
        mock_config.return_value.get_model_string.side_effect = lambda cfg: (f"{cfg['provider']}/{cfg['model']}")
        executor = TestExecutor(
            framework="drupal_runtime",
            category="runtime",
            mut_config=mut_cfg,
            judge_config=judge_cfg,
            network_config=network_cfg,
        )
    return executor


# ---------------------------------------------------------------------------
# _opencode_sessions_dir
# ---------------------------------------------------------------------------


class TestOpencodeSessionsDir:
    def test_returns_message_subdir_when_present(self, tmp_path):
        base = tmp_path / ".local" / "share" / "opencode" / "storage"
        msg_dir = base / "message"
        msg_dir.mkdir(parents=True)

        with patch("nichebench.core.executor.Path.home", return_value=tmp_path):
            result = TestExecutor._opencode_sessions_dir()

        assert result == msg_dir

    def test_returns_session_subdir_as_fallback(self, tmp_path):
        base = tmp_path / ".local" / "share" / "opencode" / "storage"
        sess_dir = base / "session"
        sess_dir.mkdir(parents=True)

        with patch("nichebench.core.executor.Path.home", return_value=tmp_path):
            result = TestExecutor._opencode_sessions_dir()

        assert result == sess_dir

    def test_returns_none_when_neither_exists(self, tmp_path):
        with patch("nichebench.core.executor.Path.home", return_value=tmp_path):
            result = TestExecutor._opencode_sessions_dir()

        assert result is None

    # ------------------------------------------------------------------
    # XDG_DATA_HOME / run-scoped storage path (Fix 1)
    # ------------------------------------------------------------------

    def test_run_scoped_xdg_data_home_used_when_provided(self, tmp_path):
        """XDG_DATA_HOME overrides the global ~/.local/share path."""
        xdg = tmp_path / "run-xdg"
        msg_dir = xdg / "opencode" / "storage" / "message"
        msg_dir.mkdir(parents=True)

        result = TestExecutor._opencode_sessions_dir(xdg_data_home=xdg)
        assert result == msg_dir

    def test_xdg_data_home_returns_none_if_storage_absent(self, tmp_path):
        """No storage dirs under XDG_DATA_HOME → graceful None."""
        xdg = tmp_path / "run-xdg"
        xdg.mkdir(parents=True)

        result = TestExecutor._opencode_sessions_dir(xdg_data_home=xdg)
        assert result is None

    def test_xdg_data_home_does_not_fall_back_to_global(self, tmp_path):
        """When XDG_DATA_HOME is given, the global ~/.local/share tree is NOT consulted."""
        xdg = tmp_path / "run-xdg"
        xdg.mkdir(parents=True)

        # Create global storage that should NOT be found
        global_msg = tmp_path / "home" / ".local" / "share" / "opencode" / "storage" / "message"
        global_msg.mkdir(parents=True)

        with patch("nichebench.core.executor.Path.home", return_value=tmp_path / "home"):
            result = TestExecutor._opencode_sessions_dir(xdg_data_home=xdg)

        assert result is None  # run-scoped dir has no storage → None, global ignored


# ---------------------------------------------------------------------------
# _snapshot_session_ids
# ---------------------------------------------------------------------------


class TestSnapshotSessionIds:
    def test_returns_empty_set_for_none(self):
        assert TestExecutor._snapshot_session_ids(None) == set()

    def test_returns_directory_names(self, tmp_path):
        (tmp_path / "sess-aaa").mkdir()
        (tmp_path / "sess-bbb").mkdir()
        (tmp_path / "file.json").write_text("{}")

        result = TestExecutor._snapshot_session_ids(tmp_path)
        assert result == {"sess-aaa", "sess-bbb"}


# ---------------------------------------------------------------------------
# _pick_newest_session
# ---------------------------------------------------------------------------


class TestPickNewestSession:
    def test_returns_newest_by_mtime(self, tmp_path):
        old_dir = tmp_path / "old-sess"
        new_dir = tmp_path / "new-sess"
        old_dir.mkdir()
        new_dir.mkdir()
        time.sleep(0.01)
        # Touch new_dir to ensure newer mtime
        new_dir.touch()

        result = TestExecutor._pick_newest_session(tmp_path, {"old-sess", "new-sess"})
        assert result == new_dir

    def test_returns_none_for_empty_set(self, tmp_path):
        result = TestExecutor._pick_newest_session(tmp_path, set())
        assert result is None


# ---------------------------------------------------------------------------
# _pick_session_by_mtime  (Fix 3 – mtime fallback)
# ---------------------------------------------------------------------------


class TestPickSessionByMtime:
    def test_returns_session_modified_within_window(self, tmp_path):
        sess = tmp_path / "sess-in-window"
        sess.mkdir()

        before = datetime.now(tz=timezone.utc)
        time.sleep(0.02)
        sess.touch()
        time.sleep(0.02)
        after = datetime.now(tz=timezone.utc)

        result = TestExecutor._pick_session_by_mtime(tmp_path, before, after)
        assert result == sess

    def test_returns_none_when_no_sessions_in_window(self, tmp_path):
        # Create session dir with old mtime (before the window)
        sess = tmp_path / "old-sess"
        sess.mkdir()

        window_start = datetime.now(tz=timezone.utc)
        time.sleep(0.02)
        window_end = datetime.now(tz=timezone.utc)
        # sess was created before window_start, so it should not be returned

        result = TestExecutor._pick_session_by_mtime(tmp_path, window_start, window_end)
        assert result is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        result = TestExecutor._pick_session_by_mtime(
            tmp_path / "nonexistent",
            datetime.now(tz=timezone.utc),
            datetime.now(tz=timezone.utc),
        )
        assert result is None


# ---------------------------------------------------------------------------
# _normalise_message
# ---------------------------------------------------------------------------


class TestNormaliseMessage:
    def test_basic_user_message(self):
        msg = TestExecutor._normalise_message({"role": "user", "content": "Hello"})
        assert msg == {"role": "user", "content": "Hello"}

    def test_list_content_joined(self):
        raw = {"role": "assistant", "content": [{"type": "text", "text": "Hi"}, {"type": "text", "text": " there"}]}
        msg = TestExecutor._normalise_message(raw)
        assert msg["content"] == "Hi there"

    def test_tool_calls_preserved(self):
        raw = {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "Bash"}}]}
        msg = TestExecutor._normalise_message(raw)
        assert "tool_calls" in msg

    def test_tool_call_id_preserved(self):
        raw = {"role": "tool", "content": "result", "tool_call_id": "tc1"}
        msg = TestExecutor._normalise_message(raw)
        assert msg["tool_call_id"] == "tc1"

    def test_missing_role_defaults_to_unknown(self):
        msg = TestExecutor._normalise_message({"content": "text"})
        assert msg["role"] == "unknown"

    def test_does_not_raise_on_empty_dict(self):
        msg = TestExecutor._normalise_message({})
        assert msg["role"] == "unknown"
        assert msg["content"] == ""


# ---------------------------------------------------------------------------
# _build_trajectory
# ---------------------------------------------------------------------------


class TestBuildTrajectory:
    def _make_session_dir(self, tmp_path, messages):
        sd = tmp_path / "sess-xyz"
        sd.mkdir()
        for i, m in enumerate(messages):
            (sd / f"{i:04d}.json").write_text(json.dumps(m))
        return sd

    def test_basic_trajectory_shape(self, tmp_path):
        executor = _make_executor()
        sd = self._make_session_dir(
            tmp_path,
            [
                {"role": "user", "content": "Do stuff"},
                {"role": "assistant", "content": "Done"},
            ],
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)

        traj = executor._build_trajectory(sd, "drupal_runtime_001", "openai/gpt-4o", start, end)

        assert traj["instance_id"] == "drupal_runtime_001"
        assert traj["model"] == "openai/gpt-4o"
        assert len(traj["messages"]) == 2
        assert traj["stats"]["total_turns"] == 2
        assert traj["stats"]["duration_seconds"] == 60.0

    def test_token_accounting(self, tmp_path):
        executor = _make_executor()
        sd = self._make_session_dir(
            tmp_path,
            [
                {"role": "assistant", "content": "x", "usage": {"input_tokens": 10, "output_tokens": 5}},
            ],
        )
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        traj = executor._build_trajectory(sd, "t1", "m", start, end)
        assert traj["stats"]["input_tokens"] == 10
        assert traj["stats"]["output_tokens"] == 5

    def test_non_numeric_token_fields_silently_ignored(self, tmp_path):
        """Non-numeric token values must not raise or drop the whole trajectory (Fix 3)."""
        executor = _make_executor()
        sd = self._make_session_dir(
            tmp_path,
            [
                {"role": "assistant", "content": "x", "usage": {"input_tokens": "N/A", "output_tokens": None}},
                {"role": "user", "content": "y"},
            ],
        )
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        traj = executor._build_trajectory(sd, "t1", "m", start, end)
        # Messages still captured; bad tokens silently zeroed
        assert len(traj["messages"]) == 2
        assert traj["stats"]["input_tokens"] == 0
        assert traj["stats"]["output_tokens"] == 0

    def test_malformed_json_file_skipped(self, tmp_path):
        executor = _make_executor()
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "bad.json").write_text("NOT JSON")
        (sd / "good.json").write_text(json.dumps({"role": "user", "content": "hi"}))
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        traj = executor._build_trajectory(sd, "t1", "m", start, end)
        assert len(traj["messages"]) == 1

    def test_empty_session_dir(self, tmp_path):
        executor = _make_executor()
        sd = tmp_path / "empty"
        sd.mkdir()
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        traj = executor._build_trajectory(sd, "t1", "m", start, end)
        assert traj["messages"] == []
        assert traj["stats"]["total_turns"] == 0


# ---------------------------------------------------------------------------
# _save_runtime_artifacts — trajectory.json persistence
# ---------------------------------------------------------------------------


class TestSaveRuntimeArtifacts:
    def _make_result(self, artifacts, retention="standard"):
        from nichebench.core.datamodel import TestCaseSpec
        from nichebench.core.executor import TestResult

        tc = TestCaseSpec(id="drupal_runtime_001", type="runtime", raw={})
        executor = _make_executor()
        executor.evaluation_config = {"runtime_artifact_retention": retention}

        result = TestResult("drupal_runtime", "runtime", tc, "openai/gpt-4o", "openai/gpt-4o")
        result.runtime_artifacts = artifacts
        return executor, result

    def test_trajectory_saved_in_standard_mode(self, tmp_path):
        trajectory = {"instance_id": "t", "model": "m", "messages": [], "stats": {}}
        artifacts = {
            "metadata.json": {},
            "final.diff": "",
            "checks.json": {},
            "trajectory.json": trajectory,
        }
        executor, result = self._make_result(artifacts, "standard")
        executor.results_outdir = tmp_path

        executor._save_runtime_artifacts(result)

        out = tmp_path / "runtime" / "drupal_runtime_001" / "trajectory.json"
        assert out.exists()
        saved = json.loads(out.read_text())
        assert saved["instance_id"] == "t"

    def test_trajectory_saved_in_full_mode(self, tmp_path):
        trajectory = {"instance_id": "t", "model": "m", "messages": [], "stats": {}}
        artifacts = {
            "metadata.json": {},
            "final.diff": "",
            "checks.json": {},
            "trajectory.json": trajectory,
        }
        executor, result = self._make_result(artifacts, "full")
        executor.results_outdir = tmp_path

        executor._save_runtime_artifacts(result)

        out = tmp_path / "runtime" / "drupal_runtime_001" / "trajectory.json"
        assert out.exists()

    def test_standard_mode_saves_run_log_checks_and_final_diff(self, tmp_path):
        artifacts = {
            "metadata.json": {"test_id": "drupal_runtime_001"},
            "run.log": "run output",
            "checks.json": {"deterministic": [{"name": "c1", "passed": True}]},
            "final.diff": "--- a/foo\n+++ b/foo",
            "trajectory.json": {"instance_id": "t", "model": "m", "messages": [], "stats": {}},
        }
        executor, result = self._make_result(artifacts, "standard")
        executor.results_outdir = tmp_path

        executor._save_runtime_artifacts(result)

        outdir = tmp_path / "runtime" / "drupal_runtime_001"
        assert (outdir / "metadata.json").exists()
        assert (outdir / "run.log").read_text(encoding="utf-8") == "run output"
        assert json.loads((outdir / "checks.json").read_text(encoding="utf-8")) == artifacts["checks.json"]
        assert (outdir / "final.diff").read_text(encoding="utf-8") == artifacts["final.diff"]
        assert (outdir / "trajectory.json").exists()

    def test_minimal_mode_saves_metadata_only(self, tmp_path):
        artifacts = {
            "metadata.json": {"test_id": "drupal_runtime_001"},
            "run.log": "run output",
            "checks.json": {"deterministic": []},
            "final.diff": "--- a/foo\n+++ b/foo",
            "trajectory.json": {"instance_id": "t", "model": "m", "messages": [], "stats": {}},
        }
        executor, result = self._make_result(artifacts, "minimal")
        executor.results_outdir = tmp_path

        executor._save_runtime_artifacts(result)

        outdir = tmp_path / "runtime" / "drupal_runtime_001"
        assert (outdir / "metadata.json").exists()
        assert not (outdir / "run.log").exists()
        assert not (outdir / "checks.json").exists()
        assert not (outdir / "final.diff").exists()
        assert not (outdir / "trajectory.json").exists()

    def test_trajectory_absent_if_not_captured(self, tmp_path):
        artifacts = {"metadata.json": {}, "final.diff": "", "checks.json": {}}
        executor, result = self._make_result(artifacts, "standard")
        executor.results_outdir = tmp_path

        executor._save_runtime_artifacts(result)

        out = tmp_path / "runtime" / "drupal_runtime_001" / "trajectory.json"
        assert not out.exists()

    def test_no_crash_when_results_outdir_none(self):
        artifacts = {"metadata.json": {}, "final.diff": "", "checks.json": {}}
        executor, result = self._make_result(artifacts, "standard")
        executor.results_outdir = None  # explicitly None
        # Should not raise
        executor._save_runtime_artifacts(result)

    def test_save_incremental_result_also_persists_runtime_artifacts(self, tmp_path):
        artifacts = {
            "metadata.json": {},
            "final.diff": "",
            "checks.json": {},
            "trajectory.json": {"instance_id": "t", "model": "m", "messages": [], "stats": {}},
        }
        executor, result = self._make_result(artifacts, "standard")
        executor.results_outdir = tmp_path / "out"
        details_path = tmp_path / "details.jsonl"

        executor.save_incremental_result(result, details_path)

        assert details_path.exists()
        trajectory_path = executor.results_outdir / "runtime" / "drupal_runtime_001" / "trajectory.json"
        assert trajectory_path.exists()

    def test_multi_trial_writes_to_trial_subdirectory(self, tmp_path):
        artifacts = {
            "metadata.json": {},
            "final.diff": "diff-content",
            "checks.json": {},
            "trajectory.json": {"instance_id": "t", "model": "m", "messages": [], "stats": {}},
        }
        executor, result = self._make_result(artifacts, "standard")
        executor.results_outdir = tmp_path
        result.trials_total = 3
        result.trial = 2

        executor._save_runtime_artifacts(result)

        out = tmp_path / "runtime" / "drupal_runtime_001" / "trial_2" / "trajectory.json"
        assert out.exists()

    def test_multi_trial_metadata_includes_trial_fields(self, tmp_path):
        artifacts = {
            "metadata.json": {"test_id": "drupal_runtime_001"},
            "final.diff": "",
            "checks.json": {},
        }
        executor, result = self._make_result(artifacts, "standard")
        executor.results_outdir = tmp_path
        result.trials_total = 3
        result.trial = 2

        executor._save_runtime_artifacts(result)

        out = tmp_path / "runtime" / "drupal_runtime_001" / "trial_2" / "metadata.json"
        assert out.exists()
        saved = json.loads(out.read_text())
        assert saved["trial"] == 2
        assert saved["trials_total"] == 3

    def test_setup_results_directory_sets_results_outdir(self, tmp_path, monkeypatch):
        executor = _make_executor()
        monkeypatch.chdir(tmp_path)

        _, _, outdir = executor.setup_results_directory({"timestamp_format": "%Y%m%d%H%M%S"})

        assert executor.results_outdir == outdir


# ---------------------------------------------------------------------------
# TestResult.to_dict — no raw artifacts payload in details.jsonl (Fix 2)
# ---------------------------------------------------------------------------


class TestToDict:
    def _make_result_with_artifacts(self, artifacts):
        from nichebench.core.datamodel import TestCaseSpec
        from nichebench.core.executor import TestResult

        tc = TestCaseSpec(id="drupal_runtime_001", type="runtime", raw={})
        result = TestResult("drupal_runtime", "runtime", tc, "openai/gpt-4o", "openai/gpt-4o")
        result.runtime_artifacts = artifacts
        return result

    def test_to_dict_emits_artifact_keys_not_raw_payload(self):
        """details.jsonl must not embed raw artifact payloads (retention leakage fix)."""
        artifacts = {
            "metadata.json": {"test_id": "drupal_runtime_001"},
            "final.diff": "--- a/foo\n+++ b/foo",
            "checks.json": {"deterministic": []},
            "trajectory.json": {"instance_id": "t", "messages": [{"role": "user", "content": "x"}]},
        }
        result = self._make_result_with_artifacts(artifacts)
        d = result.to_dict()

        # artifact_keys present and accurate
        assert "artifact_keys" in d
        assert set(d["artifact_keys"]) == set(artifacts.keys())

        # raw payload must NOT be present
        assert "artifacts" not in d

    def test_to_dict_no_artifact_keys_when_empty(self):
        """No artifact_keys key at all when runtime_artifacts is empty."""
        result = self._make_result_with_artifacts({})
        d = result.to_dict()
        assert "artifact_keys" not in d
        assert "artifacts" not in d


# ---------------------------------------------------------------------------
# _read_workspace_system_prompt
# ---------------------------------------------------------------------------


class TestReadWorkspaceSystemPrompt:
    def test_returns_prompt_from_opencode_json(self, tmp_path):
        cfg = tmp_path / "opencode.json"
        cfg.write_text(json.dumps({"mode": {"build": {"prompt": "You are a helper."}}}))
        result = TestExecutor._read_workspace_system_prompt(tmp_path)
        assert result == "You are a helper."

    def test_returns_none_when_file_missing(self, tmp_path):
        result = TestExecutor._read_workspace_system_prompt(tmp_path)
        assert result is None

    def test_returns_none_when_prompt_key_absent(self, tmp_path):
        cfg = tmp_path / "opencode.json"
        cfg.write_text(json.dumps({"mode": {"build": {}}}))
        result = TestExecutor._read_workspace_system_prompt(tmp_path)
        assert result is None

    def test_returns_none_on_malformed_json(self, tmp_path):
        cfg = tmp_path / "opencode.json"
        cfg.write_text("NOT JSON")
        result = TestExecutor._read_workspace_system_prompt(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# _build_trajectory_from_sqlite
# ---------------------------------------------------------------------------


def _create_opencode_db(
    db_path,
    session_id="sess-1",
    messages=None,
    parts=None,
    session_time=None,
    *,
    legacy_part_schema=False,
):
    """Helper to create a minimal OpenCode SQLite database for testing.

    By default creates the *real* OpenCode schema where `part` has no `type`
    column — part type is embedded inside ``part.data`` JSON as ``{"type": ...}``.

    Set ``legacy_part_schema=True`` to create the older schema with an explicit
    ``type`` column (for backward-compat tests).
    """
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("CREATE TABLE session (id TEXT PRIMARY KEY, time_created TEXT)")
    cur.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created TEXT)")
    if legacy_part_schema:
        cur.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, type TEXT, data TEXT, time_created TEXT)")
    else:
        cur.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created TEXT)")

    ts = session_time or datetime.now(tz=timezone.utc).isoformat()
    cur.execute("INSERT INTO session VALUES (?, ?)", (session_id, ts))

    for msg in messages or []:
        mid = msg.get("id", "msg-1")
        data = msg.get("data", {})
        t = msg.get("time_created", ts)
        cur.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            (mid, session_id, json.dumps(data) if isinstance(data, dict) else data, t),
        )

    for part in parts or []:
        pid = part.get("id", "part-1")
        mid = part["message_id"]
        pdata = part.get("data", {})
        pt = part.get("time_created", ts)
        if legacy_part_schema:
            ptype = part.get("type", "text")
            cur.execute(
                "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
                (pid, mid, ptype, json.dumps(pdata) if isinstance(pdata, dict) else str(pdata), pt),
            )
        else:
            # Real schema: type is inside data JSON
            cur.execute(
                "INSERT INTO part VALUES (?, ?, ?, ?)",
                (pid, mid, json.dumps(pdata) if isinstance(pdata, dict) else str(pdata), pt),
            )

    conn.commit()
    conn.close()


class TestBuildTrajectoryFromSqlite:
    def test_returns_none_when_db_missing(self, tmp_path):
        db = tmp_path / "nonexistent.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )
        assert result is None

    def test_builds_trajectory_from_message_rows(self, tmp_path):
        db = tmp_path / "opencode.db"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {"id": "msg-1", "data": {"role": "user", "content": "Do stuff"}, "time_created": start.isoformat()},
                {"id": "msg-2", "data": {"role": "assistant", "content": "Done"}, "time_created": end.isoformat()},
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="drupal_runtime_001",
            model_str="openai/gpt-4o",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        assert result["instance_id"] == "drupal_runtime_001"
        assert result["model"] == "openai/gpt-4o"
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"
        assert result["stats"]["total_turns"] == 2
        assert result["stats"]["duration_seconds"] == 60.0

    def test_includes_thinking_when_reasoning_part_exists(self, tmp_path):
        """Thinking captured from reasoning parts (real schema: type inside part.data)."""
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {"id": "msg-1", "data": {"role": "assistant"}, "time_created": start.isoformat()},
            ],
            parts=[
                {
                    "id": "part-1",
                    "message_id": "msg-1",
                    "data": {"type": "reasoning", "text": "I should think about this..."},
                    "time_created": start.isoformat(),
                },
                {
                    "id": "part-2",
                    "message_id": "msg-1",
                    "data": {"type": "text", "text": "regular text part"},
                    "time_created": start.isoformat(),
                },
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        msg = result["messages"][0]
        assert "thinking" in msg
        assert len(msg["thinking"]) == 1
        assert msg["thinking"][0]["text"] == "I should think about this..."
        # All parts should still be present
        assert "parts" in msg
        assert len(msg["parts"]) == 2

    def test_includes_system_prompt_when_passed(self, tmp_path):
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {"id": "msg-1", "data": {"role": "user", "content": "hi"}, "time_created": start.isoformat()},
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
            system_prompt="You are a Drupal developer.",
        )

        assert result is not None
        assert result["system_prompt"] == "You are a Drupal developer."

    def test_no_system_prompt_key_when_none(self, tmp_path):
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {"id": "msg-1", "data": {"role": "user", "content": "hi"}, "time_created": start.isoformat()},
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
            system_prompt=None,
        )

        assert result is not None
        assert "system_prompt" not in result

    def test_token_accounting_from_usage(self, tmp_path):
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {
                    "id": "msg-1",
                    "data": {
                        "role": "assistant",
                        "content": "x",
                        "usage": {"input_tokens": 100, "output_tokens": 50},
                    },
                    "time_created": start.isoformat(),
                },
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        assert result["stats"]["input_tokens"] == 100
        assert result["stats"]["output_tokens"] == 50

    def test_returns_none_on_empty_db(self, tmp_path):
        """Database exists but has no sessions → returns None."""
        db = tmp_path / "opencode.db"
        import sqlite3

        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, time_created TEXT)")
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created TEXT)")
        conn.commit()
        conn.close()

        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )
        assert result is None

    # ------------------------------------------------------------------
    # Real-schema tests (no `type` column in part table)
    # ------------------------------------------------------------------

    def test_content_reconstructed_from_text_parts(self, tmp_path):
        """When message.data has no content, it is rebuilt from text parts (real schema)."""
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {"id": "msg-1", "data": {"role": "assistant"}, "time_created": start.isoformat()},
            ],
            parts=[
                {
                    "id": "part-1",
                    "message_id": "msg-1",
                    "data": {"type": "text", "text": "Hello from parts"},
                    "time_created": start.isoformat(),
                },
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        msg = result["messages"][0]
        assert msg["content"] == "Hello from parts"

    def test_content_reconstructed_from_multiple_text_parts(self, tmp_path):
        """Multiple text parts are joined with newline."""
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {"id": "msg-1", "data": {"role": "assistant"}, "time_created": start.isoformat()},
            ],
            parts=[
                {
                    "id": "part-1",
                    "message_id": "msg-1",
                    "data": {"type": "text", "text": "First line"},
                    "time_created": start.isoformat(),
                },
                {
                    "id": "part-2",
                    "message_id": "msg-1",
                    "data": {"type": "text", "text": "Second line"},
                    "time_created": start.isoformat(),
                },
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        msg = result["messages"][0]
        assert msg["content"] == "First line\nSecond line"

    def test_thinking_from_reasoning_parts_real_schema(self, tmp_path):
        """Reasoning parts populate msg['thinking'] (real schema: type in part.data)."""
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {"id": "msg-1", "data": {"role": "assistant"}, "time_created": start.isoformat()},
            ],
            parts=[
                {
                    "id": "part-1",
                    "message_id": "msg-1",
                    "data": {"type": "reasoning", "text": "Let me think..."},
                    "time_created": start.isoformat(),
                },
                {
                    "id": "part-2",
                    "message_id": "msg-1",
                    "data": {"type": "reasoning", "text": "Also consider..."},
                    "time_created": start.isoformat(),
                },
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        msg = result["messages"][0]
        assert "thinking" in msg
        assert len(msg["thinking"]) == 2
        assert msg["thinking"][0]["text"] == "Let me think..."
        assert msg["thinking"][1]["text"] == "Also consider..."

    def test_token_accounting_real_schema(self, tmp_path):
        """Token accounting handles real schema: tokens.input / tokens.output."""
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {
                    "id": "msg-1",
                    "data": {
                        "role": "assistant",
                        "tokens": {"input": 200, "output": 75},
                    },
                    "time_created": start.isoformat(),
                },
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        assert result["stats"]["input_tokens"] == 200
        assert result["stats"]["output_tokens"] == 75

    def test_full_real_schema_message_with_parts_and_tokens(self, tmp_path):
        """Integration: real schema with no content in message.data, parts, and tokens."""
        db = tmp_path / "opencode.db"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 2, 0, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {
                    "id": "msg-1",
                    "data": {"role": "user", "content": "Please fix the module"},
                    "time_created": start.isoformat(),
                },
                {
                    "id": "msg-2",
                    "data": {
                        "role": "assistant",
                        "tokens": {"input": 500, "output": 120},
                    },
                    "time_created": end.isoformat(),
                },
            ],
            parts=[
                {
                    "id": "part-1",
                    "message_id": "msg-2",
                    "data": {"type": "reasoning", "text": "I need to edit the module file..."},
                    "time_created": start.isoformat(),
                },
                {
                    "id": "part-2",
                    "message_id": "msg-2",
                    "data": {"type": "text", "text": "I've fixed the module by..."},
                    "time_created": start.isoformat(),
                },
                {
                    "id": "part-3",
                    "message_id": "msg-2",
                    "data": {"type": "tool-invocation", "toolName": "Edit"},
                    "time_created": start.isoformat(),
                },
            ],
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="drupal_runtime_001",
            model_str="groq/llama-3.3-70b",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        assert result["instance_id"] == "drupal_runtime_001"
        assert len(result["messages"]) == 2

        # First message: user with content from data
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "Please fix the module"

        # Second message: assistant, content rebuilt from text part
        assert result["messages"][1]["role"] == "assistant"
        assert result["messages"][1]["content"] == "I've fixed the module by..."
        assert "thinking" in result["messages"][1]
        assert result["messages"][1]["thinking"][0]["text"] == "I need to edit the module file..."
        assert len(result["messages"][1]["parts"]) == 3

        # Token accounting from real schema
        assert result["stats"]["input_tokens"] == 500
        assert result["stats"]["output_tokens"] == 120
        assert result["stats"]["duration_seconds"] == 120.0

    def test_backward_compat_legacy_type_column_still_works(self, tmp_path):
        """Legacy DB with explicit `type` column still parses correctly."""
        db = tmp_path / "opencode.db"
        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _create_opencode_db(
            db,
            session_time=start.isoformat(),
            messages=[
                {"id": "msg-1", "data": {"role": "assistant"}, "time_created": start.isoformat()},
            ],
            parts=[
                {
                    "id": "part-1",
                    "message_id": "msg-1",
                    "type": "reasoning",
                    "data": {"text": "legacy thinking"},
                    "time_created": start.isoformat(),
                },
                {
                    "id": "part-2",
                    "message_id": "msg-1",
                    "type": "text",
                    "data": {"text": "legacy content"},
                    "time_created": start.isoformat(),
                },
            ],
            legacy_part_schema=True,
        )

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        msg = result["messages"][0]
        assert msg["content"] == "legacy content"
        assert msg["thinking"][0]["text"] == "legacy thinking"

    def test_legacy_sessions_table_fallback(self, tmp_path):
        """When primary 'session' table absent, fallback uses legacy 'sessions'/'messages' tables."""
        import sqlite3

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT)")
        conn.execute(
            "CREATE TABLE messages ("
            "id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, "
            "tool_calls TEXT, created_at TEXT)"
        )
        conn.execute("INSERT INTO sessions VALUES ('sess-1', '2026-01-01T00:00:00+00:00')")
        conn.execute(
            "INSERT INTO messages VALUES "
            "('msg-1', 'sess-1', 'user', 'Hello legacy', NULL, '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO messages VALUES "
            "('msg-2', 'sess-1', 'assistant', 'Hi from legacy', NULL, '2026-01-01T00:01:00+00:00')"
        )
        conn.commit()
        conn.close()

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t-legacy",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is not None
        assert result["instance_id"] == "t-legacy"
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "Hello legacy"
        assert result["messages"][1]["role"] == "assistant"
        assert result["messages"][1]["content"] == "Hi from legacy"

    def test_legacy_sessions_fallback_with_system_prompt(self, tmp_path):
        """Legacy schema fallback propagates system_prompt when provided."""
        import sqlite3

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT)")
        conn.execute(
            "CREATE TABLE messages ("
            "id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, "
            "tool_calls TEXT, created_at TEXT)"
        )
        conn.execute("INSERT INTO sessions VALUES ('sess-1', '2026-01-01T00:00:00+00:00')")
        conn.execute(
            "INSERT INTO messages VALUES " "('msg-1', 'sess-1', 'user', 'task', NULL, '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()

        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
            system_prompt="You are a Drupal developer.",
        )

        assert result is not None
        assert result["system_prompt"] == "You are a Drupal developer."

    def test_legacy_sessions_returns_none_when_sessions_absent(self, tmp_path):
        """When both 'session' and 'sessions' tables are absent, returns None."""
        import sqlite3

        db = tmp_path / "unknown.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unrelated (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        start = end = datetime(2026, 1, 1, tzinfo=timezone.utc)

        result = TestExecutor._build_trajectory_from_sqlite(
            db_path=db,
            test_case_id="t1",
            model_str="m1",
            start_time=start,
            end_time=end,
        )

        assert result is None
