"""Configuration loader for nichebench."""

import math
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    litellm_api_key: str | None = os.getenv("LITELLM_API_KEY")
    default_timeout: int = int(
        os.getenv("NICH_BENCH_TIMEOUT", "600")
    )  # Increased to 10 minutes for large token generation
    retry_attempts: int = int(os.getenv("NICH_BENCH_RETRY_ATTEMPTS", "5"))
    retry_delay: float = float(os.getenv("NICH_BENCH_RETRY_DELAY", "3.0"))  # seconds between retries
    judge_threshold: float = float(os.getenv("NICHEBENCH_JUDGE_THRESHOLD", "0.7"))
    results_dir: str = os.getenv("NICH_BENCH_RESULTS_DIR", "results")


settings = Settings()


def resolve_judge_threshold(evaluation_config: Optional[Mapping[str, Any]] = None) -> float:
    """Effective pass threshold for direct code/bug judge scores.

    ``evaluation.judge_threshold`` (the top-level value merged with the
    active profile — see
    ``NicheBenchConfig.get_evaluation_config(profile=...)``) wins whenever it
    is a finite float inside ``[0, 1]``.  Otherwise the environment-backed
    ``Settings.judge_threshold`` (``NICHEBENCH_JUDGE_THRESHOLD``, default
    ``0.7``) applies, so a config that never mentions the key keeps the
    behaviour it had before the key existed.

    Args:
        evaluation_config: Effective evaluation block, or ``None``.

    Returns:
        The threshold as a float in ``[0, 1]``.
    """
    if isinstance(evaluation_config, Mapping):
        raw = evaluation_config.get("judge_threshold")
        if raw is not None and not isinstance(raw, bool):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
            if value is not None and math.isfinite(value) and 0.0 <= value <= 1.0:
                return value
    return settings.judge_threshold
