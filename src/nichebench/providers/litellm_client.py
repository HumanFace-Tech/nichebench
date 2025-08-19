"""Minimal litellm client wrapper stub. Replace with real litellm client in future."""

import time


class LiteLLMClient:
    def __init__(self, api_key: str | None = None, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout

    def generate(self, prompt: str, model: str = "gpt-3.5-turbo") -> dict:
        # Minimal stub: echo back prompt and pretend it's an LLM response.
        time.sleep(0.01)
        return {"model": model, "output": prompt[:1000]}
