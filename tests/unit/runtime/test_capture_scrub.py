"""Scrubbing policy tests: what must be scrubbed, and what must survive.

The archive is fine-tuning material, so over-redaction is a defect: numeric
usage counters, config keys, and ordinary ``token``-named data have to reach
disk intact.
"""

from __future__ import annotations

from nichebench.execution.runtime.capture.scrub import (
    REDACTED,
    is_credential_key,
    redact_credential_keys,
    scrub_known_credentials,
    scrub_mapping_known_credentials,
    scrub_text_formats,
)

SECRET = "sk-live-9f8e7d6c5b4a3210"


class TestCredentialKeyDetection:
    def test_credential_keys_are_recognised(self):
        for key in (
            "api_key",
            "API-KEY",
            "apiKey",
            "authorization",
            "Authorization",
            "password",
            "client_secret",
            "access_token",
            "refresh_token",
            "private_key",
            "minimax_api_key",
            "token",
        ):
            assert is_credential_key(key), key

    def test_ordinary_keys_are_not_credentials(self):
        for key in (
            "token_count",
            "input_tokens",
            "output_tokens",
            "max_tokens",
            "usage",
            "cost",
            "content",
            "model",
            "boundary",
            "temperature",
            "limit",
            "context",
            "MAX_TOKENS",
            "session_id",
        ):
            assert not is_credential_key(key), key


class TestStructuralRedaction:
    def test_credential_key_values_are_replaced(self):
        payload = {"api_key": SECRET, "Authorization": SECRET, "password": "hunter2"}
        scrubbed = redact_credential_keys(payload)
        assert set(scrubbed.values()) == {REDACTED}

    def test_numeric_usage_and_config_survive(self):
        payload = {
            "usage": {"input_tokens": 1234, "output_tokens": 567, "cost": 0.0},
            "limit": {"context": 1048576, "output": 32768},
            "temperature": 0.0,
            "max_tokens": 32768,
            "token_count": 4096,
            "token": 123,
        }
        assert redact_credential_keys(payload) == payload

    def test_nested_structures_are_walked(self):
        payload = {"a": [{"b": {"api_key": SECRET, "input_tokens": 5}}]}
        scrubbed = redact_credential_keys(payload)
        assert scrubbed["a"][0]["b"]["api_key"] == REDACTED
        assert scrubbed["a"][0]["b"]["input_tokens"] == 5

    def test_non_string_values_are_untouched(self):
        payload = {"cost": 0.0, "stop": True, "values": [1, 2, 3], "nothing": None}
        assert redact_credential_keys(payload) == payload


class TestExactCredentialScrubbing:
    def test_known_credential_is_replaced_and_reported(self):
        text = f"Authorization: Bearer {SECRET}"
        scrubbed, hits = scrub_known_credentials(text, [SECRET])
        assert SECRET not in scrubbed
        assert hits == [SECRET]

    def test_absent_credential_leaves_text_byte_identical(self):
        text = "ordinary agent output with token: settings.get('token')"
        scrubbed, hits = scrub_known_credentials(text, [SECRET])
        assert scrubbed == text
        assert hits == []

    def test_even_short_explicitly_known_credentials_are_scrubbed(self):
        text = "the value is abc"
        scrubbed, hits = scrub_known_credentials(text, ["abc"])
        assert scrubbed == "the value is [REDACTED]"
        assert hits == ["abc"]

    def test_multiple_known_credentials_are_all_replaced(self):
        other = "Bearer-another-long-token-value"
        text = f"{SECRET} and {other}"
        scrubbed, hits = scrub_known_credentials(text, [SECRET, other])
        assert SECRET not in scrubbed and other not in scrubbed
        assert set(hits) == {SECRET, other}


class TestTextFormatScrubbing:
    def test_env_style_credentials(self):
        for text in (
            "OPENAI_API_KEY=sk-abc123456",
            "GROQ_API_KEY=gsk-abc123456",
            "MINIMAX_API_KEY=eyJhbGciOi-abc",
        ):
            scrubbed, hits = scrub_text_formats(text)
            assert "REDACTED" in scrubbed, text
            assert hits

    def test_bearer_headers(self):
        scrubbed, hits = scrub_text_formats("Authorization: Bearer abc.def.ghi")
        assert "abc.def.ghi" not in scrubbed
        assert hits

    def test_json_credential_values_stay_valid_json(self):
        text = '{"apiKey": "sk-json-value-1234", "model": "MiniMax-M3"}'
        scrubbed, hits = scrub_text_formats(text)
        assert "sk-json-value-1234" not in scrubbed
        assert hits
        import json

        assert json.loads(scrubbed) == {"apiKey": REDACTED, "model": "MiniMax-M3"}

    def test_json_token_count_is_not_scrubbed(self):
        text = '{"input_tokens": 4321, "max_tokens": 32768}'
        scrubbed, hits = scrub_text_formats(text)
        assert scrubbed == text
        assert hits == []

    def test_prose_mentioning_token_survives(self):
        text = "the agent read config token: settings.get('token') and MAX_TOKENS=32768"
        scrubbed, hits = scrub_text_formats(text)
        assert scrubbed == text
        assert hits == []

    def test_bare_api_key_assignment(self):
        scrubbed, hits = scrub_text_formats("apiKey=sk-inline-value-123")
        assert "sk-inline-value-123" not in scrubbed
        assert hits


class TestMappingScrubbing:
    def test_headers_and_leaves_are_scrubbed(self):
        payload = {
            "headers": {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"},
            "body": {"api_key": SECRET, "input_tokens": 10},
        }
        scrubbed, hits = scrub_mapping_known_credentials(payload, [SECRET])
        assert scrubbed["headers"]["Authorization"] == REDACTED
        assert scrubbed["headers"]["Content-Type"] == "application/json"
        assert scrubbed["body"]["api_key"] == REDACTED
        assert scrubbed["body"]["input_tokens"] == 10
        assert SECRET not in str(scrubbed)

    def test_no_hits_means_no_redaction_marker(self):
        payload = {"headers": {"Content-Type": "application/json"}}
        scrubbed, hits = scrub_mapping_known_credentials(payload, [SECRET])
        assert hits == []
        assert scrubbed == payload
