"""Tests for LiteLLM client retry mechanism."""

import time
from unittest.mock import MagicMock, patch

import pytest

from nichebench.providers.litellm_client import LiteLLMClient


class TestLiteLLMClientRetry:
    """Test retry logic using LiteLLM's built-in retry mechanism."""

    def test_client_initialization_with_custom_settings(self):
        """Test that client initializes with custom retry settings."""
        client = LiteLLMClient(timeout=90, retry_attempts=7, retry_delay=2.5)

        assert client.timeout == 90
        assert client.retry_attempts == 7
        assert client.retry_delay == 2.5

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    def test_retry_configuration_passed_to_litellm(self, mock_litellm_module):
        """Test that retry configuration is passed to LiteLLM's completion."""
        client = LiteLLMClient(retry_attempts=3, timeout=60)
        client.litellm_available = True

        # Mock successful response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Success!"))]
        mock_litellm_module.completion.return_value = mock_response

        client.generate("test prompt", "test/model")

        # Verify that num_retries and timeout are passed to completion
        mock_litellm_module.completion.assert_called_once()
        call_args = mock_litellm_module.completion.call_args[1]
        assert call_args["num_retries"] == 3
        assert call_args["timeout"] == 60

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    def test_success_on_first_try(self, mock_litellm_module):
        """Test that successful calls work as expected."""
        client = LiteLLMClient(retry_attempts=3, retry_delay=0.01)

        # Mock successful response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Success!"))]
        mock_litellm_module.completion.return_value = mock_response

        result = client.generate("test prompt", "test/model")

        # Should have tried only once
        assert mock_litellm_module.completion.call_count == 1
        assert result["output"] == "Success!"

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE", None)
    def test_fallback_when_litellm_unavailable(self):
        """Test fallback behavior when LiteLLM is not available."""
        client = LiteLLMClient(retry_attempts=3)

        result = client.generate("test prompt", "test/model")

        assert "[Error: model did not return a response]" in result["output"]

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    def test_error_handling_with_litellm_retries(self, mock_litellm_module):
        """Test that LiteLLM errors are handled after its internal retries."""
        client = LiteLLMClient(retry_attempts=3, retry_delay=0.01)

        # Mock litellm to fail even after its internal retries
        mock_litellm_module.completion.side_effect = Exception("Rate limit exceeded")

        result = client.generate("test prompt", "test/model")

        # LiteLLM should have handled retries internally, we just get the final error
        assert mock_litellm_module.completion.call_count == 1
        assert "Rate limit exceeded" in result["output"]
        assert f"after {client.retry_attempts} attempts" in result["output"]

    def test_parameter_merging_with_retry_settings(self):
        """Test that retry settings don't interfere with parameter merging."""
        client = LiteLLMClient(retry_attempts=5, retry_delay=1.0)
        client.litellm_available = False  # Use stub

        model_params = {"temperature": 0.5, "max_tokens": 1000}
        resp = client.generate(
            "test",
            model="test/model",
            model_params=model_params,
            temperature=0.8,  # Should override model_params
            top_p=0.9,
        )

        # Stub just returns error, but parameters should be processed
        assert resp["output"] == "[Error: model did not return a response]"

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    def test_streaming_disabled_with_retries(self, mock_litellm_module):
        """Test that streaming is disabled and retry settings are preserved."""
        client = LiteLLMClient(retry_attempts=2, timeout=30)
        client.litellm_available = True

        # Mock successful response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Success!"))]
        mock_litellm_module.completion.return_value = mock_response

        client.generate("test prompt", "test/model")

        # Verify streaming is disabled and retry settings are passed
        call_args = mock_litellm_module.completion.call_args[1]
        assert call_args["stream"] is False
        assert call_args["num_retries"] == 2
        assert call_args["timeout"] == 30
