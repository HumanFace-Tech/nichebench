"""Lightweight LiteLLM client wrapper used by NicheBench.

This file provides a small wrapper around the actual `litellm` package when
available. In CI / offline dev we keep a minimal fallback stub so tests don't
require network calls.
"""

import json
import random
import time
from typing import Any

from nichebench.utils.io import strip_think_tags

try:
    import litellm

    LITELLM_AVAILABLE = True
    LITELLM_MODULE = litellm
except Exception:  # pragma: no cover - optional dependency
    LITELLM_AVAILABLE = False
    LITELLM_MODULE = None


class LiteLLMClient:
    def __init__(
        self, api_key: str | None = None, timeout: int = 120, retry_attempts: int = 5, retry_delay: float = 3.0
    ):
        self.api_key = api_key
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
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
        # Convert simple prompt to messages format
        messages = [{"role": "user", "content": prompt}]
        return self.generate_with_messages(messages, model, model_params=model_params, **kwargs)

    def generate_with_messages(
        self,
        messages: list[dict[str, str]],
        model: str = "openai/gpt-5",
        *,
        model_params: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Generate text using conversation messages format.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            model: Model identifier (provider/model format)
            model_params: Dictionary of model parameters (temperature, max_tokens, etc.)
            **kwargs: Additional parameters for backward compatibility

        Returns a dict with at least `model` and `output` keys.
        """
        # Merge parameters with precedence: kwargs > model_params > defaults
        params = model_params.copy() if model_params else {}
        params.update(kwargs)

        # Extract API base URL for custom endpoints (like Ollama)
        api_base = params.pop("api_base", None)

        # For large prompts, keep streaming disabled to avoid org verification issues
        use_streaming = False  # Disabled due to OpenAI org verification requirements

        if self.litellm_available and LITELLM_MODULE:
            try:
                # Use litellm.completion() API with built-in retry
                # Filter and adjust parameters for model compatibility
                filtered_params = self._filter_model_parameters(model, params)

                # Build completion arguments
                completion_args = {
                    "model": model,
                    "messages": messages,  # Use provided messages instead of converting prompt
                    "timeout": self.timeout,
                    "num_retries": self.retry_attempts,  # Use LiteLLM's built-in retry
                    "stream": use_streaming,  # Enable streaming for large prompts
                }

                # Add filtered parameters
                completion_args.update(filtered_params)

                # Add API base for custom endpoints (Ollama, local servers, etc.)
                if api_base:
                    completion_args["api_base"] = api_base

                response = LITELLM_MODULE.completion(**completion_args)

                # Handle streaming vs non-streaming responses
                if use_streaming:
                    content_parts = []
                    for chunk in response:
                        if hasattr(chunk, "choices") and getattr(chunk, "choices", None):
                            first_choice = chunk.choices[0]  # type: ignore
                            if hasattr(first_choice, "delta") and getattr(first_choice, "delta", None):
                                delta_content = getattr(first_choice.delta, "content", None)  # type: ignore
                                if delta_content:
                                    content_parts.append(delta_content)

                    content = "".join(content_parts)
                    return {"model": model, "output": strip_think_tags(content)}
                else:
                    # Regular non-streaming response
                    if hasattr(response, "choices") and getattr(response, "choices", None):
                        first_choice = response.choices[0]  # type: ignore
                        if hasattr(first_choice, "message") and getattr(first_choice, "message", None):
                            content = getattr(first_choice.message, "content", None)  # type: ignore
                            if content:
                                return {"model": model, "output": strip_think_tags(content)}
                        # Alternative structure handling
                        content = str(first_choice)
                        return {"model": model, "output": strip_think_tags(content)}
                    else:
                        return {"model": model, "output": strip_think_tags(str(response))}

            except Exception as e:
                # LiteLLM handles retries internally, so if we get here, all retries failed
                error_msg = f"[Error: LiteLLM error after {self.retry_attempts} attempts: {e}]"
                print(f"DEBUG: {error_msg}")
                return {"model": model, "output": error_msg}

        # Fallback stub: return a clear error marker rather than echoing the
        # prompt. Echoing made it indistinguishable from a successful model
        # response in tests and real runs. Tests that rely on the echo
        # behavior should be updated to expect an explicit error marker.
        time.sleep(0.01)
        return {"model": model, "output": "[Error: model did not return a response]"}

    def _filter_model_parameters(self, model: str, parameters: dict) -> dict:
        """Filter parameters based on model capabilities to avoid API errors."""
        filtered_params = parameters.copy()

        # Handle OpenAI GPT-5 specific constraints
        if "gpt-5" in model.lower():
            # GPT-5 requires temperature=1.0 and doesn't support certain parameters
            filtered_params = {
                "temperature": 1.0,  # Required to be exactly 1.0
                "max_tokens": parameters.get("max_tokens", 1024),
            }
            # Keep only known supported reasoning parameters for GPT-5
            for key in ["reasoning_effort"]:  # Remove reasoning_format and reasoning_steps
                if key in parameters:
                    filtered_params[key] = parameters[key]

        return filtered_params


def parse_json_safe(text: str) -> Any:
    """Attempt to parse JSON from text. If text contains code fences or extra
    text, try to extract the first valid JSON object.

    This handles cases where:
    - JSON is wrapped in code fences (```json ... ```)
    - JSON is embedded in longer explanatory text
    - JSON has nested braces and complex structure
    """
    text = text.strip()

    # Try direct JSON parsing first
    try:
        return json.loads(text)
    except Exception:
        pass

    # Look for JSON in code fences (```json ... ``` or ``` ... ```)
    import re

    # Try to find JSON in code fences
    fence_patterns = [r"```json\s*\n(.*?)\n```", r"```\s*\n(.*?)\n```", r"```json(.*?)```", r"```(.*?)```"]

    for pattern in fence_patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match.strip())
            except Exception:
                continue

    # Try to extract JSON objects more carefully by counting braces
    def extract_json_objects(text):
        """Extract potential JSON objects by balancing braces."""
        results = []
        i = 0
        while i < len(text):
            if text[i] == "{":
                # Found start of potential JSON object
                brace_count = 0
                start = i
                j = i
                while j < len(text):
                    if text[j] == "{":
                        brace_count += 1
                    elif text[j] == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            # Found complete JSON object
                            potential_json = text[start : j + 1]
                            try:
                                parsed = json.loads(potential_json)
                                results.append(parsed)
                                i = j + 1
                                break
                            except Exception:
                                pass
                    j += 1
                else:
                    # Didn't find closing brace, move on
                    i += 1
            else:
                i += 1
        return results

    # Try to extract JSON objects
    json_objects = extract_json_objects(text)
    if json_objects:
        # Return the first valid JSON object that looks like our expected structure
        for obj in json_objects:
            if isinstance(obj, dict) and ("criteria" in obj or "pass" in obj or "score" in obj):
                return obj
        # If no structured object found, return the first one
        return json_objects[0]

    # Last resort: try simple regex patterns for common JSON structures
    patterns = [
        r'\{[^{}]*"criteria"[^{}]*\[[^\]]*\][^{}]*\}',  # Look for criteria arrays
        r'\{[^{}]*"overall_score"[^{}]*\}',  # Look for overall_score
        r'\{[^{}]*"pass"[^{}]*\}',  # Look for pass/fail
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match)
            except Exception:
                continue

    return text
