"""Tests for configuration loading with network settings."""

import tempfile
from pathlib import Path

import pytest
import yaml

from nichebench.config.nichebench_config import NicheBenchConfig
from nichebench.config.settings import Settings


class TestNetworkConfiguration:
    """Test network configuration loading and integration."""

    def test_default_network_settings(self):
        """Test that default network settings are applied."""
        config = NicheBenchConfig()
        network_config = config.get_network_config()

        expected_defaults = {"timeout": 600, "retry_attempts": 5, "retry_delay": 3.0}

        for key, expected_value in expected_defaults.items():
            assert network_config[key] == expected_value, f"Default {key} should be {expected_value}"

    def test_custom_yaml_network_settings(self):
        """Test loading custom network settings from YAML."""
        custom_config = {"network": {"timeout": 180, "retry_attempts": 3, "retry_delay": 5.0}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(custom_config, f)
            config_path = Path(f.name)

        try:
            config = NicheBenchConfig(config_path)
            network_config = config.get_network_config()

            assert network_config["timeout"] == 180
            assert network_config["retry_attempts"] == 3
            assert network_config["retry_delay"] == 5.0
        finally:
            config_path.unlink()

    def test_partial_network_settings_merge_with_defaults(self):
        """Test that partial network settings merge with defaults."""
        partial_config = {"network": {"timeout": 90}}  # Only override timeout

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(partial_config, f)
            config_path = Path(f.name)

        try:
            config = NicheBenchConfig(config_path)
            network_config = config.get_network_config()

            # Custom setting
            assert network_config["timeout"] == 90

            # Default settings should remain
            assert network_config["retry_attempts"] == 5
            assert network_config["retry_delay"] == 3.0
        finally:
            config_path.unlink()

    def test_environment_variable_settings(self):
        """Test that Settings class reads from environment variables."""
        import importlib
        import os

        from nichebench.config import settings as settings_module

        # Temporarily set environment variables
        old_timeout = os.environ.get("NICH_BENCH_TIMEOUT")
        old_attempts = os.environ.get("NICH_BENCH_RETRY_ATTEMPTS")
        old_delay = os.environ.get("NICH_BENCH_RETRY_DELAY")

        try:
            os.environ["NICH_BENCH_TIMEOUT"] = "150"
            os.environ["NICH_BENCH_RETRY_ATTEMPTS"] = "7"
            os.environ["NICH_BENCH_RETRY_DELAY"] = "2.5"

            # Reload the settings module to pick up new env vars
            importlib.reload(settings_module)

            settings = settings_module.settings

            assert settings.default_timeout == 150
            assert settings.retry_attempts == 7
            assert settings.retry_delay == 2.5

        finally:
            # Restore original environment
            for var, old_value in [
                ("NICH_BENCH_TIMEOUT", old_timeout),
                ("NICH_BENCH_RETRY_ATTEMPTS", old_attempts),
                ("NICH_BENCH_RETRY_DELAY", old_delay),
            ]:
                if old_value is not None:
                    os.environ[var] = old_value
                elif var in os.environ:
                    del os.environ[var]

            # Reload settings back to original state
            importlib.reload(settings_module)

    def test_invalid_network_config_values(self):
        """Test handling of invalid configuration values."""
        invalid_config = {"network": {"timeout": "not_a_number", "retry_attempts": -1, "retry_delay": "invalid"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(invalid_config, f)
            config_path = Path(f.name)

        try:
            # Should not raise an exception, just use the invalid values as-is
            # (validation would typically happen at runtime)
            config = NicheBenchConfig(config_path)
            network_config = config.get_network_config()

            # The YAML loader should preserve the types as loaded
            assert network_config["timeout"] == "not_a_number"
            assert network_config["retry_attempts"] == -1
            assert network_config["retry_delay"] == "invalid"

        finally:
            config_path.unlink()

    def test_missing_network_section_uses_defaults(self):
        """Test that missing network section falls back to defaults."""
        config_without_network = {"mut": {"provider": "openai", "model": "gpt-4"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_without_network, f)
            config_path = Path(f.name)

        try:
            config = NicheBenchConfig(config_path)
            network_config = config.get_network_config()

            # Should use defaults
            assert network_config["timeout"] == 600
            assert network_config["retry_attempts"] == 5
            assert network_config["retry_delay"] == 3.0

        finally:
            config_path.unlink()

    def test_empty_config_file_uses_defaults(self):
        """Test that empty config file uses all defaults."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("")  # Empty file
            config_path = Path(f.name)

        try:
            config = NicheBenchConfig(config_path)
            network_config = config.get_network_config()

            # Should use all defaults
            assert network_config["timeout"] == 600
            assert network_config["retry_attempts"] == 5
            assert network_config["retry_delay"] == 3.0

        finally:
            config_path.unlink()
