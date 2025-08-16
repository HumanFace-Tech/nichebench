# Load environment variables
import os
from pathlib import Path

# Try to load from .env.local first, then .env
env_files = [".env.local", ".env"]
for env_file in env_files:
    env_path = Path(__file__).parent / env_file
    if env_path.exists():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        if value and not value.startswith(
                            "your-"
                        ):  # Only set if not placeholder
                            os.environ[key.strip()] = value.strip()
        except Exception:
            pass  # Ignore errors in env loading
        break
