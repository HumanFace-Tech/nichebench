"""Application-boundary (judge lane) capture tests and scope isolation."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from nichebench.providers.litellm_client import LiteLLMClient, _current_app_capture, app_capture_scope
from tests.unit.runtime.capture_helpers import events, events_of, make_session


class _FakeChoiceMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeChoiceMessage(content)


class _FakeResponse:
    """Stand-in for a LiteLLM response object."""

    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = {"prompt_tokens": 11, "completion_tokens": 7}


class TestAppCaptureScope:
    def test_scope_is_empty_by_default(self):
        assert _current_app_capture() is None

    def test_scope_binds_then_resets(self, tmp_path):
        session = make_session(tmp_path)
        with app_capture_scope(session, lane="judge", label="score_runtime"):
            assert _current_app_capture() is session
            assert session.current_lane == "judge"
            assert session.current_label == "score_runtime"
        assert _current_app_capture() is None

    def test_scope_resets_on_exception(self, tmp_path):
        session = make_session(tmp_path)
        with pytest_raises(RuntimeError), app_capture_scope(session):
            raise RuntimeError("boom")
        assert _current_app_capture() is None

    def test_nested_scopes_restore_outer_session(self, tmp_path):
        outer = make_session(tmp_path)
        inner = make_session(tmp_path / "inner")
        with app_capture_scope(outer, label="outer"):
            with app_capture_scope(inner, label="inner"):
                assert _current_app_capture() is inner
            assert _current_app_capture() is outer
        assert _current_app_capture() is None

    def test_capture_does_not_mutate_litellm_api_base(self, tmp_path):
        import litellm

        before = getattr(litellm, "api_base", None)
        session = make_session(tmp_path)
        with app_capture_scope(session):
            assert getattr(litellm, "api_base", None) == before
        assert getattr(litellm, "api_base", None) == before


class TestJudgeLaneCapture:
    def _client(self) -> LiteLLMClient:
        return LiteLLMClient(timeout=5, retry_attempts=1, retry_delay=0)

    def test_arguments_and_response_are_captured(self, tmp_path):
        session = make_session(tmp_path)
        client = self._client()
        litellm_module = MagicMock()
        litellm_module.completion.return_value = _FakeResponse("looks good")

        import nichebench.providers.litellm_client as client_module

        original = client_module.LITELLM_MODULE
        client_module.LITELLM_MODULE = litellm_module
        try:
            with app_capture_scope(session, lane="judge", label="score_runtime"):
                result = client.generate_with_messages(
                    messages=[{"role": "user", "content": "grade this"}],
                    model="minimax/MiniMax-M2.7",
                    model_params={"temperature": 0.3, "max_tokens": 512},
                )
        finally:
            client_module.LITELLM_MODULE = original

        assert result["output"] == "looks good"
        attempt = session.run.judges[0]
        records = events(attempt.path)
        request = events_of(records, "application_request")
        response = events_of(records, "blob_stored")
        stored = events_of(records, "application_response_recorded")
        assert request and response and stored
        assert response[0]["payload_name"] == "application_response"
        assert request[0]["lane"] == "judge"
        assert request[0]["model"] == "minimax/MiniMax-M2.7"
        assert request[0]["arguments"]["messages"][0]["content"] == "grade this"
        assert request[0]["arguments"]["temperature"] == 0.3
        assert request[0]["boundary"] == "application_response"
        assert "not a wire capture" in request[0]["boundary_note"]
        journal = session.journal_for(attempt, judge=True)
        payload = json.loads(journal.blobs.get(response[0]["payload_ref"]))
        assert "looks good" in json.dumps(payload)
        assert response[0]["before_strip"] is True
        assert stored[0]["before_strip"] is True
        assert stored[0]["payload_ref"] == response[0]["payload_ref"]
        assert stored[0]["model"] == "minimax/MiniMax-M2.7"

    def test_credential_arguments_are_removed_and_marked(self, tmp_path):
        session = make_session(tmp_path, known_credentials=["sk-live-abcdef123456"])
        client = self._client()
        litellm_module = MagicMock()
        litellm_module.completion.return_value = _FakeResponse("fine")

        import nichebench.providers.litellm_client as client_module

        original = client_module.LITELLM_MODULE
        client_module.LITELLM_MODULE = litellm_module
        try:
            with app_capture_scope(session, lane="judge", label="score_runtime"):
                client.generate_with_messages(
                    messages=[{"role": "user", "content": "hi"}],
                    model="minimax/MiniMax-M2.7",
                    model_params={"api_key": "sk-live-abcdef123456", "temperature": 0.1},
                )
        finally:
            client_module.LITELLM_MODULE = original

        attempt = session.run.judges[0]
        records = events(attempt.path)
        request = events_of(records, "application_request")[0]
        assert request["arguments"]["api_key"] == "[REDACTED]"
        assert request["arguments"]["temperature"] == 0.1
        # The redaction is structural: the credential-named key is replaced, so
        # no literal value is needed in the capture's known-credential set.
        assert "sk-live-abcdef123456" not in attempt.events_path.read_text(encoding="utf-8")
        stored = events_of(records, "application_response_recorded")
        journal = session.journal_for(attempt, judge=True)
        assert stored
        assert "sk-live-abcdef123456" not in journal.blobs.get(stored[0]["payload_ref"]).decode("utf-8")

    def test_errors_are_captured_without_repr_leak(self, tmp_path):
        session = make_session(tmp_path)
        client = self._client()
        litellm_module = MagicMock()
        litellm_module.completion.side_effect = RuntimeError("upstream said no")

        import nichebench.providers.litellm_client as client_module

        original = client_module.LITELLM_MODULE
        client_module.LITELLM_MODULE = litellm_module
        try:
            with app_capture_scope(session, lane="judge", label="score_runtime"):
                result = client.generate_with_messages(
                    messages=[{"role": "user", "content": "hi"}],
                    model="minimax/MiniMax-M2.7",
                )
        finally:
            client_module.LITELLM_MODULE = original

        assert result["output"].startswith("[Error:")
        attempt = session.run.judges[0]
        errors = events_of(events(attempt.path), "application_error")
        assert errors
        assert errors[0]["error_type"] == "RuntimeError"
        # No object repr, message text, or credentials in the recorded error.
        assert "object at 0x" not in json.dumps(errors[0])
        assert "upstream said no" not in attempt.events_path.read_text(encoding="utf-8")

    def test_no_capture_scope_records_nothing(self, tmp_path):
        session = make_session(tmp_path)
        client = self._client()
        litellm_module = MagicMock()
        litellm_module.completion.return_value = _FakeResponse("ok")

        import nichebench.providers.litellm_client as client_module

        original = client_module.LITELLM_MODULE
        client_module.LITELLM_MODULE = litellm_module
        try:
            client.generate_with_messages(
                messages=[{"role": "user", "content": "hi"}],
                model="minimax/MiniMax-M2.7",
            )
        finally:
            client_module.LITELLM_MODULE = original

        assert session.run.judges == []

    def test_multiple_judge_samples_each_land_in_the_journal(self, tmp_path):
        session = make_session(tmp_path)
        client = self._client()
        litellm_module = MagicMock()
        litellm_module.completion.return_value = _FakeResponse("ok")

        import nichebench.providers.litellm_client as client_module

        original = client_module.LITELLM_MODULE
        client_module.LITELLM_MODULE = litellm_module
        try:
            with app_capture_scope(session, lane="judge", label="score_runtime"):
                for _ in range(3):
                    client.generate_with_messages(
                        messages=[{"role": "user", "content": "hi"}],
                        model="minimax/MiniMax-M2.7",
                    )
        finally:
            client_module.LITELLM_MODULE = original

        records = events(session.run.judges[0].path)
        assert len(events_of(records, "application_request")) == 3
        assert len(events_of(records, "application_response_recorded")) == 3


def pytest_raises(exc: type) -> Any:
    """Tiny helper so the scope tests read cleanly."""
    import pytest

    return pytest.raises(exc)
