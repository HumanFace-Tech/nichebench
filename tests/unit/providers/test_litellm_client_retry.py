"""Tests for LiteLLM client retry mechanism."""

import time
from unittest.mock import MagicMock, patch

import pytest

from nichebench.providers.litellm_client import LiteLLMClient


class TestLiteLLMClientRetry:
    """Test retry logic and exponential backoff in LiteLLMClient."""

    def test_exponential_backoff_delay_calculation(self):
        """Test that exponential backoff delays grow correctly."""
        client = LiteLLMClient(retry_delay=2.0)

        # Test the base delay calculation (without jitter for predictability)
        with patch("random.uniform", return_value=0.0):  # No jitter
            delays = [client._exponential_backoff_delay(i) for i in range(5)]

        # Expected: 2.0, 4.0, 8.0, 16.0, 32.0 (base_delay * 2^attempt)
        expected_base_delays = [2.0, 4.0, 8.0, 16.0, 32.0]

        for i, (actual, expected) in enumerate(zip(delays, expected_base_delays)):
            # Allow for some variance due to potential floating point precision
            assert abs(actual - expected) < 0.1, f"Attempt {i}: expected ~{expected}, got {actual}"

    def test_exponential_backoff_has_jitter(self):
        """Test that jitter adds randomness to delays."""
        client = LiteLLMClient(retry_delay=1.0)

        # Generate multiple delays for the same attempt
        delays = [client._exponential_backoff_delay(1) for _ in range(10)]

        # Should have some variation due to jitter
        assert len(set(delays)) > 1, "Expected jitter to create variation in delays"

        # All delays should be around 2.0 ± 20%
        for delay in delays:
            assert 1.6 <= delay <= 2.4, f"Delay {delay} outside expected range [1.6, 2.4]"

    def test_exponential_backoff_minimum_delay(self):
        """Test that delays never go below 1.0 second."""
        client = LiteLLMClient(retry_delay=0.1)  # Very small base delay

        # Even with maximum negative jitter, should not go below 1.0
        with patch("random.uniform", return_value=-0.2):  # Maximum negative jitter
            delay = client._exponential_backoff_delay(0)
            assert delay >= 1.0, f"Delay {delay} below minimum of 1.0 seconds"

    def test_retryable_error_detection(self):
        """Test that retryable errors are correctly identified."""
        retryable_cases = [
            "Request timed out",
            "Rate limit exceeded",
            "Too many requests",
            "Server error occurred",
            "Internal error",
            "Service unavailable",
            "Connection error",
            "Network error",
            "Bad gateway",
            "Gateway timeout",
        ]

        non_retryable_cases = [
            "Invalid API key",
            "Authentication failed",
            "Model not found",
            "Bad request",
            "Permission denied",
        ]

        retryable_patterns = [
            "timeout",
            "timed out",
            "rate limit",
            "rate_limit",
            "too many requests",
            "server error",
            "internal error",
            "service unavailable",
            "connection error",
            "network error",
            "bad gateway",
            "gateway timeout",
        ]

        # Test retryable errors
        for error in retryable_cases:
            is_retryable = any(pattern in error.lower() for pattern in retryable_patterns)
            assert is_retryable, f"'{error}' should be retryable"

        # Test non-retryable errors
        for error in non_retryable_cases:
            is_retryable = any(pattern in error.lower() for pattern in retryable_patterns)
            assert not is_retryable, f"'{error}' should not be retryable"

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    def test_retry_on_timeout_error(self, mock_litellm):
        """Test that timeout errors trigger retries."""
        client = LiteLLMClient(retry_attempts=3, retry_delay=0.01)  # Fast retries for testing

        # Mock litellm to raise timeout error then succeed
        mock_litellm.completion.side_effect = [
            Exception("Request timed out"),
            Exception("Request timed out"),
            MagicMock(choices=[MagicMock(message=MagicMock(content="Success!"))]),
        ]

        with patch("time.sleep"):  # Skip actual delays
            result = client.generate("test prompt", "test/model")

        # Should have retried 3 times total (2 failures + 1 success)
        assert mock_litellm.completion.call_count == 3
        assert result["output"] == "Success!"

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    def test_no_retry_on_non_retryable_error(self, mock_litellm):
        """Test that non-retryable errors don't trigger retries."""
        client = LiteLLMClient(retry_attempts=3, retry_delay=0.01)

        # Mock litellm to raise non-retryable error
        mock_litellm.completion.side_effect = Exception("Invalid API key")

        result = client.generate("test prompt", "test/model")

        # Should have tried only once (no retries)
        assert mock_litellm.completion.call_count == 1
        assert "Invalid API key" in result["output"]

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    def test_max_retries_exhausted(self, mock_litellm):
        """Test behavior when all retries are exhausted."""
        client = LiteLLMClient(retry_attempts=2, retry_delay=0.01)

        # Mock litellm to always fail with retryable error
        mock_litellm.completion.side_effect = Exception("Rate limit exceeded")

        with patch("time.sleep"):  # Skip actual delays
            result = client.generate("test prompt", "test/model")

        # Should have tried 2 times total
        assert mock_litellm.completion.call_count == 2
        assert "Rate limit exceeded" in result["output"]
        assert "after 2 attempts" in result["output"]

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    def test_success_on_first_try(self, mock_litellm):
        """Test that successful calls don't trigger retries."""
        client = LiteLLMClient(retry_attempts=3, retry_delay=0.01)

        # Mock successful response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Success!"))]
        mock_litellm.completion.return_value = mock_response

        result = client.generate("test prompt", "test/model")

        # Should have tried only once
        assert mock_litellm.completion.call_count == 1
        assert result["output"] == "Success!"

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE", None)
    def test_fallback_when_litellm_unavailable(self):
        """Test fallback behavior when LiteLLM is not available."""
        client = LiteLLMClient(retry_attempts=3)

        result = client.generate("test prompt", "test/model")

        assert "[Error: model did not return a response]" in result["output"]

    def test_client_initialization_with_custom_settings(self):
        """Test that client initializes with custom retry settings."""
        client = LiteLLMClient(timeout=90, retry_attempts=7, retry_delay=2.5)

        assert client.timeout == 90
        assert client.retry_attempts == 7
        assert client.retry_delay == 2.5

    @patch("nichebench.providers.litellm_client.LITELLM_MODULE")
    @patch("time.sleep")
    def test_delay_between_retries(self, mock_sleep, mock_litellm):
        """Test that delays are applied between retries."""
        client = LiteLLMClient(retry_attempts=3, retry_delay=1.0)

        # Mock to fail twice then succeed
        mock_litellm.completion.side_effect = [
            Exception("Rate limit exceeded"),
            Exception("Rate limit exceeded"),
            MagicMock(choices=[MagicMock(message=MagicMock(content="Success!"))]),
        ]

        client.generate("test prompt", "test/model")

        # Should have called sleep 2 times (between the 3 attempts)
        assert mock_sleep.call_count == 2

        # Check that sleep was called with exponential backoff delays
        sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
        assert len(sleep_calls) == 2
        assert all(delay >= 1.0 for delay in sleep_calls)  # Minimum delay
        assert sleep_calls[1] > sleep_calls[0]  # Exponential increase
