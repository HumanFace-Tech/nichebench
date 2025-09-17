"""Configuration management for NicheBench."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class NicheBenchConfig:
    """Manages configuration loading and model parameter resolution."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path("nichebench.yml")
        self._config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML file with fallback to defaults."""
        if self.config_path.exists():
            with open(self.config_path, "r") as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {}

        # Apply defaults
        self._apply_defaults()

    def _apply_defaults(self) -> None:
        """Apply sensible defaults for missing configuration."""
        defaults = {
            "mut": {
                "provider": "groq",
                "model": "gemma2-9b-it",
                "parameters": {"temperature": 0.0, "max_tokens": 4096, "top_p": 1.0},
            },
            "judge": {
                "provider": "openai",
                "model": "gpt-5",
                "parameters": {"temperature": 1.0, "max_tokens": 1024},  # Removed top_p for GPT-5 compatibility
            },
            "evaluation": {
                "save_full_prompts": True,
                "save_raw_outputs": True,
                "parallel_workers": 4,
                "parallelism": 1,  # Default to sequential execution
            },
            "network": {"timeout": 600, "retry_attempts": 5, "retry_delay": 3.0},
            "results": {"auto_report": True, "save_format": "jsonl", "timestamp_format": "%Y%m%d_%H%M%S"},
        }

        # Deep merge defaults with loaded config
        self._config = self._deep_merge(defaults, self._config)

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Deep merge two dictionaries."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get_mut_config(self, model_override: Optional[str] = None, profile: Optional[str] = None) -> Dict[str, Any]:
        """Get MUT model configuration with CLI overrides."""
        config = self._config["mut"].copy()

        # Apply profile if specified
        if profile and "profiles" in self._config and profile in self._config["profiles"]:
            profile_config = self._config["profiles"][profile].get("mut", {})
            config.update(profile_config)

        # Apply CLI model override
        if model_override:
            if "/" in model_override:
                provider, model = model_override.split("/", 1)
                config["provider"] = provider
                config["model"] = model
            else:
                config["model"] = model_override

        # Auto-configure Ollama defaults if provider is ollama
        self._apply_ollama_defaults(config)

        return config

    def get_judge_config(self, judge_override: Optional[str] = None, profile: Optional[str] = None) -> Dict[str, Any]:
        """Get judge model configuration with CLI overrides."""
        config = self._config["judge"].copy()

        # Apply profile if specified
        if profile and "profiles" in self._config and profile in self._config["profiles"]:
            profile_config = self._config["profiles"][profile].get("judge", {})
            config.update(profile_config)

        # Apply CLI judge override
        if judge_override:
            if "/" in judge_override:
                provider, model = judge_override.split("/", 1)
                config["provider"] = provider
                config["model"] = model
            else:
                config["model"] = judge_override

        # Fallback to environment variable
        if not judge_override:
            env_judge = os.environ.get("NICHEBENCH_JUDGE")
            if env_judge and "/" in env_judge:
                provider, model = env_judge.split("/", 1)
                config["provider"] = provider
                config["model"] = model

        return config

    def get_evaluation_config(self) -> Dict[str, Any]:
        """Get evaluation settings."""
        return self._config["evaluation"].copy()

    def get_network_config(self) -> Dict[str, Any]:
        """Get network settings."""
        return self._config["network"].copy()

    def get_results_config(self) -> Dict[str, Any]:
        """Get results settings."""
        return self._config["results"].copy()

    def get_model_string(self, config: Dict[str, Any]) -> str:
        """Convert model config to provider/model string."""
        return f"{config['provider']}/{config['model']}"

    def list_profiles(self) -> list[str]:
        """List available configuration profiles."""
        return list(self._config.get("profiles", {}).keys())

    def _apply_ollama_defaults(self, config: Dict[str, Any]) -> None:
        """Apply Ollama-specific defaults when provider is 'ollama'."""
        if config.get("provider") != "ollama":
            return

        # Ensure parameters exist
        if "parameters" not in config:
            config["parameters"] = {}

        # Set default API base for Ollama if not specified
        if "api_base" not in config["parameters"]:
            config["parameters"]["api_base"] = "http://localhost:11434"

        # Ollama typically doesn't need some parameters, but we'll let LiteLLM handle it
        # Set reasonable defaults for Ollama
        params = config["parameters"]
        if "temperature" not in params:
            params["temperature"] = 0.0
        if "max_tokens" not in params:
            params["max_tokens"] = 4096


# Global config instance
_config_instance: Optional[NicheBenchConfig] = None


def get_config() -> NicheBenchConfig:
    """Get the global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = NicheBenchConfig()
    return _config_instance


def reload_config(config_path: Optional[Path] = None) -> NicheBenchConfig:
    """Reload configuration from file."""
    global _config_instance
    _config_instance = NicheBenchConfig(config_path)
    return _config_instance
