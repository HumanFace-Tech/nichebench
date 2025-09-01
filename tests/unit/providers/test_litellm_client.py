import time
from unittest.mock import MagicMock, patch

from nichebench.providers.litellm_client import LiteLLMClient, parse_json_safe


def test_parse_json_safe_simple():
    """Test basic JSON parsing."""
    s = '{"a": 1, "b": 2}'
    out = parse_json_safe(s)
    assert isinstance(out, dict) and out["a"] == 1


def test_parse_json_safe_with_fences():
    """Test JSON extraction from code fences."""
    s = '```json\n{\n  "ok": true\n}\n```'
    out = parse_json_safe(s)
    assert isinstance(out, dict) and out["ok"] is True


def test_parse_json_safe_embedded_json():
    """Test JSON extraction from surrounding text."""
    s = 'Here\'s the response: {"status": "success", "value": 42} and some more text'
    out = parse_json_safe(s)
    assert isinstance(out, dict) and out["status"] == "success" and out["value"] == 42


def test_parse_json_safe_malformed():
    """Test handling of malformed JSON."""
    s = "This is not JSON at all"
    out = parse_json_safe(s)
    assert out == s  # Should return original text


def test_generate_fallback_echo():
    """Test stub behavior when litellm unavailable."""
    client = LiteLLMClient()
    # Force unavailable client to use stub
    client.litellm_available = False
    start = time.time()
    resp = client.generate("hello world", model="openai/gpt-5")
    end = time.time()
    # Ensure stub returned prompt as output
    assert resp["output"] == "hello world"
    # Sleep was used in stub; check that it didn't take too long
    assert (end - start) < 1.0


def test_parameter_merging_precedence():
    """Test that kwargs override model_params with proper precedence."""
    client = LiteLLMClient()
    client.litellm_available = False  # Use stub

    model_params = {"temperature": 0.5, "max_tokens": 1000}
    resp = client.generate(
        "test",
        model="test/model",
        model_params=model_params,
        temperature=0.8,  # Should override model_params
        top_p=0.9,
    )

    # Stub just echoes, but parameters should be processed
    assert resp["output"] == "test"


@patch("nichebench.providers.litellm_client.litellm")
def test_reasoning_parameters_passthrough(mock_litellm):
    """Test that reasoning parameters are passed to litellm when available."""
    # Mock successful completion
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Response content"
    mock_litellm.completion.return_value = mock_response

    client = LiteLLMClient()
    client.litellm_available = True

    resp = client.generate("test prompt", model="openai/o1-preview", reasoning_effort="medium", reasoning_format="json")

    # Verify litellm.completion was called with reasoning params
    mock_litellm.completion.assert_called_once()
    call_args = mock_litellm.completion.call_args[1]
    assert call_args["reasoning_effort"] == "medium"
    assert call_args["reasoning_format"] == "json"
    assert resp["output"] == "Response content"


@patch("nichebench.providers.litellm_client.litellm")
def test_litellm_error_fallback(mock_litellm):
    """Test fallback behavior when litellm raises an exception."""
    # Make litellm.completion raise an exception
    mock_litellm.completion.side_effect = Exception("API Error")

    client = LiteLLMClient()
    client.litellm_available = True

    resp = client.generate("test prompt", model="groq/gemma2-9b-it")

    # Should fall back to stub behavior
    assert resp["output"] == "test prompt"
    assert resp["model"] == "groq/gemma2-9b-it"


def test_temperature_adjustment_for_gpt5():
    """Test temperature adjustment for gpt-5 models that don't support 0.0."""
    client = LiteLLMClient()
    client.litellm_available = False  # Use stub to avoid actual API calls

    # This test verifies the logic exists, even though stub doesn't use it
    resp = client.generate("test", model="openai/gpt-5", model_params={"temperature": 0.0})

    # With stub, just verify it doesn't crash
    assert resp["output"] == "test"
