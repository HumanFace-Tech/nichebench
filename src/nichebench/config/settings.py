"""Configuration loader for nichebench."""

import os
from dataclasses import dataclass

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
    results_dir: str = os.getenv("NICH_BENCH_RESULTS_DIR", "results")


settings = Settings()
