"""Lightweight LiteLLM client wrapper used by NicheBench.

This file provides a small wrapper around the actual `litellm` package when
available. In CI / offline dev we keep a minimal fallback stub so tests don't
require network calls.
"""

import json
import time
from typing import Any

try:
    import litellm

    LITELLM_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    LITELLM_AVAILABLE = False


class LiteLLMClient:
    def __init__(self, api_key: str | None = None, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout
        self.litellm_available = LITELLM_AVAILABLE

    def generate(
        self, prompt: str, model: str = "openai/gpt-5", *, model_params: dict[str, Any] | None = None, **kwargs
    ) -> dict[str, Any]:
        """Generate text from the underlying litellm client or fallback stub.

        Args:
            prompt: The input prompt text
            model: Model identifier (provider/model format)
            model_params: Dictionary of model parameters (temperature, max_tokens, etc.)
            **kwargs: Additional parameters for backward compatibility

        Returns a dict with at least `model` and `output` keys. If a real client
        exists, returns its response converted to a dict.
        """
        # Merge parameters with precedence: kwargs > model_params > defaults
        params = model_params.copy() if model_params else {}
        params.update(kwargs)

        # Extract common parameters with defaults
        temperature = params.get("temperature", 0.0)
        max_tokens = params.get("max_tokens", 4096)
        top_p = params.get("top_p", 1.0)

        if self.litellm_available:
            try:
                # Use litellm.completion() API directly
                # Adjust temperature for models that don't support 0.0
                temp = 1.0 if "gpt-5" in model and temperature == 0.0 else temperature

                # Build completion arguments
                completion_args = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temp,
                    "max_tokens": max_tokens,
                    "top_p": top_p,
                    "timeout": self.timeout,
                }

                # Add any additional parameters that litellm supports
                litellm_params = [
                    "presence_penalty",
                    "frequency_penalty",
                    "reasoning_effort",
                    "reasoning_format",
                    "max_completion_tokens",
                    "stream",
                    "stop",
                ]
                for param in litellm_params:
                    if param in params and params[param] is not None:
                        completion_args[param] = params[param]

                response = litellm.completion(**completion_args)

                # Extract content from response
                if hasattr(response, "choices") and response.choices:
                    content = response.choices[0].message.content
                    return {"model": model, "output": content}
                else:
                    return {"model": model, "output": str(response)}
            except Exception as e:
                # Fall back to stub on any error
                print(f"LiteLLM error: {e}, falling back to stub")
                pass

        # Fallback stub: echo prompt and wrap in a dict.
        # NOTE: do not arbitrarily truncate the output here. Some tasks
        # (code generation, long-form outputs) can produce very large
        # strings; truncating to 2000 chars hid bugs and made the stub
        # unrealistic. If you need to limit memory for a special case,
        # handle it at the call site.
        time.sleep(0.01)
        return {"model": model, "output": prompt}


def parse_json_safe(text: str) -> Any:
    """Attempt to parse JSON from text. If text contains code fences or extra
    text, try to extract the first JSON object.
    """
    text = text.strip()
    # Strip common code fences
    if text.startswith("```"):
        # remove fences
        parts = text.split("\n", 1)
        if len(parts) > 1:
            text = parts[1]
            if text.endswith("```"):
                text = text[:-3]
    # Try direct JSON
    try:
        return json.loads(text)
    except Exception:
        # Attempt to find a JSON substring
        import re

        m = re.search(r"(\{[\s\S]*\})", text)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return text
        return text
