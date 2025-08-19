"""Configuration loader for nichebench."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    litellm_api_key: str | None = os.getenv("LITELLM_API_KEY")
    default_timeout: int = int(os.getenv("NICH_BENCH_TIMEOUT", "30"))
    results_dir: str = os.getenv("NICH_BENCH_RESULTS_DIR", "results")


settings = Settings()
