"""Lightweight LiteLLM client wrapper used by NicheBench.

Ownership
---------
This module wraps the ``litellm`` package to provide a consistent generation
interface for the benchmark harness. ``litellm`` is an **optional dependency**;
the module ships with a minimal fallback stub so that tests and offline dev
environments do not require network access or an API key.

Non-ownership
------------
``litellm`` is not owned by this module or by NicheBench. Its API surface,
retry behaviour, and error semantics are controlled by the upstream
``litellm`` package. Any breaking changes in ``litellm`` may require updates
to this wrapper.

Caller expectations
-------------------
Callers must not assume that a response is always well-formed model output.
The fallback stub returns an explicit error marker (see below). Callers are
responsible for checking the shape of the returned dict.

Offline fallback behaviour
--------------------------
When ``litellm`` is unavailable (e.g. in CI without credentials) the client
returns::

    {"model": <model>, "output": "[Error: model did not return a response]"}

This is an **intentional** design choice: the fallback deliberately does not
echo the prompt, because doing so is indistinguishable from a genuine model
response in tests and production artefacts. Tests that require a specific
fallback shape must be updated to expect the explicit error marker.

``litellm.api_base`` reset constraint
------------------------------------
``litellm`` uses a module-level global (``litellm.api_base``) to control the
endpoint for OpenAI-compatible requests. This wrapper temporarily sets that
global for custom ``api_base`` calls (e.g. Ollama, local servers) and **always
restores the previous value in the ``finally`` block** of
``generate_with_messages``. This invariant must be preserved: any caller that
modifies ``litellm.api_base`` outside this wrapper risks corrupting the state
seen by concurrent or subsequent calls.
"""

import json
import logging
import time
from typing import Any

from nichebench.utils.io import strip_think_tags

_logger = logging.getLogger(__name__)

try:
    import litellm

    LITELLM_AVAILABLE = True
    LITELLM_MODULE = litellm
except Exception:  # pragma: no cover - optional dependency
    LITELLM_AVAILABLE = False
    LITELLM_MODULE = None


class LiteLLMClient:
    """Lightweight LiteLLM client wrapper.

    This client provides a ``generate`` / ``generate_with_messages`` interface
    that targets ``litellm`` when available and falls back to an explicit error
    marker when it is not. The client does **not** own the ``litellm`` module —
    it is a thin adapter that:

    1. Normalises prompt-based calls to the messages format expected by
       ``litellm.completion``.
    2. Handles custom ``api_base`` endpoints (e.g. Ollama, local servers) by
       temporarily patching ``litellm.api_base`` for the duration of a single
       call and restoring it in ``finally``.
    3. Applies model-specific parameter filtering (e.g. GPT-5 constraints).
    4. Strips ``<think>`` / ``</think>`` tags from model output via
       ``strip_think_tags``.

    Attributes:
        api_key: API key passed to ``litellm.completion``. May be ``None`` for
            custom endpoints that ignore it.
        timeout: Request timeout in seconds, passed to ``litellm``.
        retry_attempts: Number of retries on transient failures. This maps to
            ``litellm``'s ``num_retries`` and is handled internally by
            ``litellm``, not by this client directly.
        retry_delay: Base delay between retries (seconds). Note: ``litellm``
            controls the actual retry schedule; this value is accepted for
            API compatibility but the effective delay is determined by
            ``litellm``.
        litellm_available: ``True`` when the ``litellm`` module was successfully
            imported; ``False`` when the fallback stub is active.

    Raises:
        The underlying ``litellm.completion`` call may raise arbitrary
        exceptions which are caught and converted into an error marker dict.
    """

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
            prompt: The input prompt text.
            model: Model identifier in ``provider/model`` format
                (e.g. ``openai/gpt-4o``, ``groq/llama-3.3-70b-versatile``).
            model_params: Model parameters such as ``temperature``,
                ``max_tokens``, ``reasoning_effort``, etc. Passed to
                ``litellm.completion`` after model-specific filtering.
            **kwargs: Additional parameters merged into ``model_params`` for
                backward compatibility.

        Returns:
            ``dict`` with at least two keys:

            - ``model`` (``str``): the model identifier as passed in.
            - ``output`` (``str``): the generated text, or an error marker
              string when ``litellm`` is unavailable or all retries failed.

        Retry behaviour:
            ``retry_attempts`` is forwarded to ``litellm``'s ``num_retries``.
            ``litellm`` handles retry scheduling internally. If all retries
            are exhausted, the exception is caught and an error marker dict
            is returned (the ``output`` field contains the error message).

        Output contract:
            The ``output`` field is **not guaranteed to be non-empty**. When
            ``max_tokens`` is too small for the model to emit any token, the
            model may return ``None`` content, which this method converts to
            an empty string. Callers must handle both empty and non-empty
            ``output`` values.

        Error marker (offline / all-retries-failed):
            ``{"model": <model>, "output": "[Error: model did not return a response]"}``
            or
            ``{"model": <model>, "output": "[Error: LiteLLM error after <N> attempts: <detail>]"}``
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
            messages: List of message dicts each containing ``role`` and
                ``content`` keys (e.g. ``{"role": "user", "content": "..."}``).
            model: Model identifier in ``provider/model`` format.
            model_params: Model parameters such as ``temperature``,
                ``max_tokens``, ``api_base``, etc. ``api_base`` is extracted
                before the call and must not be forwarded to
                ``litellm.completion`` directly.
            **kwargs: Additional parameters merged into ``model_params`` for
                backward compatibility.

        Returns:
            ``dict`` with at least ``model`` and ``output`` keys. See
            :meth:`generate` for the full output contract and error markers.

        Retry behaviour:
            Identical to :meth:`generate`. ``retry_attempts`` is forwarded to
            ``litellm``'s ``num_retries``; ``litellm`` handles the retry schedule.

        ``api_base`` handling:
            When ``api_base`` is present in ``model_params`` it is:

            1. Stripped from the call arguments (it must not reach
               ``litellm.completion`` directly).
            2. Normalised to a ``/v1`` suffix if not already present, because
                ``litellm``'s OpenAI provider appends ``/chat/completions``
               without the ``/v1`` prefix.
            3. Forwarded to ``litellm.completion`` via the per-call
               ``api_base`` argument; the module-level ``litellm.api_base``
               global is **not** mutated.  Per-call routing avoids any
               cross-request contamination under ``parallelism > 1``.

            The provider prefix is stripped from the ``model`` name for custom
            endpoints (e.g. ``openai/llama-3.3-70b`` becomes ``llama-3.3-70b``)
            because custom endpoints only accept bare model IDs.

        ``litellm.api_base`` invariant:
            This method does **not** mutate the module-level global.  Callers
            that need to set a global endpoint for downstream calls should do
            so explicitly, not by routing through this method.
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
                    # Ensure /v1 suffix so OpenAI-compatible endpoints hit the right path.
                    # LiteLLM's OpenAI provider appends /chat/completions without /v1/,
                    # so we pre-pend it here. Also strip any provider prefix from the
                    # bare model name since custom endpoints only accept bare model IDs
                    # (e.g. llama-swap rejects "openai/qwen3.6-35b-a3b").
                    normalized_base = api_base.rstrip("/")
                    if not normalized_base.endswith("/v1"):
                        normalized_base += "/v1"
                    # Per-call api_base — does NOT mutate the global.  This
                    # keeps the harness safe under parallelism and removes
                    # the need for a finally-clobber dance on the module
                    # global.  See ``litellm.api_base`` invariant above.
                    completion_args["api_base"] = normalized_base
                    # Provide a dummy api_key so litellm's OpenAI handler doesn't
                    # reject the request for missing API key. Custom endpoints like
                    # llama-swap/Ollama ignore this field.
                    if not completion_args.get("api_key"):
                        completion_args["api_key"] = "dummy"
                    model_for_api = model
                    if "/" in model:
                        # Strip provider prefix so bare model name reaches the endpoint
                        model_for_api = model.split("/", 1)[1]
                    # Always use OpenAI-compatible client for custom api_base endpoints
                    completion_args["custom_llm_provider"] = "openai"
                    completion_args["model"] = model_for_api

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
                # Regular non-streaming response
                if hasattr(response, "choices") and getattr(response, "choices", None):
                    first_choice = response.choices[0]  # type: ignore
                    if hasattr(first_choice, "message") and getattr(first_choice, "message", None):
                        raw_content = getattr(first_choice.message, "content", None)
                        # raw_content is None when max_tokens was too small for model to emit any token
                        return {"model": model, "output": strip_think_tags(raw_content) if raw_content else ""}
                    # Alternative structure handling
                    content = str(first_choice)
                    return {"model": model, "output": strip_think_tags(content)}
                return {"model": model, "output": strip_think_tags(str(response))}

            except Exception as e:
                # LiteLLM handles retries internally, so if we get here, all retries failed.
                # Route through the harness logger at debug level so raw exception
                # text (which may include provider URLs / request details) is
                # filtered by the same logging/redaction policy as other
                # harness output, instead of going straight to stdout.
                error_msg = f"[Error: LiteLLM error after {self.retry_attempts} attempts: {e}]"
                _logger.debug("litellm_call_failed: %s", error_msg)
                return {"model": model, "output": error_msg}

        # Fallback stub: return a clear error marker rather than echoing the
        # prompt. Echoing made it indistinguishable from a successful model
        # response in tests and real runs. Tests that rely on the echo
        # behavior should be updated to expect an explicit error marker.
        time.sleep(0.01)
        return {"model": model, "output": "[Error: model did not return a response]"}

    def _filter_model_parameters(self, model: str, parameters: dict) -> dict:
        """Filter parameters based on model capabilities to avoid API errors.

        This is a private adapter that adjusts the ``parameters`` dict before it
        is forwarded to ``litellm.completion``. It exists because some models
        have constraints that are not automatically handled by ``litellm``.

        Currently modelled constraints:

        - **GPT-5**: Requires ``temperature`` to be exactly ``1.0`` and does not
          support arbitrary parameter sets. Only ``max_tokens`` and
          ``reasoning_effort`` are forwarded; all other parameters are dropped.

        Args:
            model: Model identifier string (checked via case-insensitive
                substring match, e.g. ``"gpt-5"``).
            parameters: Full parameter dict as passed to
                ``generate_with_messages``.

        Returns:
            A filtered ``dict`` safe to pass to ``litellm.completion``.
            For unknown models the input ``parameters`` dict is returned
            unchanged (modulo the copy).
        """
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
    """Parse JSON from text that may be wrapped or embedded.

    This is a best-effort extractor used when model output contains JSON in
    contexts where the model may have wrapped it in prose, code fences, or
    surrounding explanatory text.

    Extraction strategy (in order):

    1. **Direct parse**: Attempt ``json.loads`` on the stripped input.
    2. **Code fences**: Look for the first match of
       `````json\\n...\\n``` ``, `` ```\\n...\\n``` ``,
       `````json...``` ``, or `` ```...``` `` and parse the extracted content.
    3. **Balanced braces**: Scan for the first top-level JSON object whose
       braces balance; parse it.  Returns at the first match rather than
       accumulating all valid objects, to bound memory and CPU cost.
    4. **Regex fallback**: Attempt targeted regex patterns for common
       structures (``criteria`` arrays, ``overall_score``, ``pass``/``score``
       fields).

    Priority when multiple JSON objects are found in step 3:
    The first object that contains ``criteria``, ``pass``, or ``score`` is
    returned. If none match, the first valid object is returned.

    Args:
        text: Raw text that may contain JSON.

    Returns:
        The parsed JSON object (``dict``, ``list``, or primitive) on success.
        On complete failure returns the **original string** ``text`` unchanged.

    Warning:
        This function swallows most parse errors silently. Callers must
        check the return type (``isinstance(result, str)``) when the caller
        needs to distinguish between a genuine string and a parsed object.
    """
    text = text.strip()
    import re

    # Cap input size to prevent pathological/adversarial judge output from
    # consuming unbounded CPU/memory during lenient parsing.  Real judge
    # responses are well under this limit.
    _MAX_PARSE_INPUT = 1_000_000  # 1 MB
    if len(text) > _MAX_PARSE_INPUT:
        text = text[:_MAX_PARSE_INPUT]

    invalid_json_escape = re.compile(r'\\(?!["\\/bfnrtu])')

    def loads_lenient(candidate: str) -> Any:
        """Parse JSON, repairing common LLM-produced invalid backslash escapes."""
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return json.loads(invalid_json_escape.sub(r"\\\\", candidate))

    # Try direct JSON parsing first
    try:
        return loads_lenient(text)
    except Exception:
        pass

    # Look for JSON in code fences (```json ... ``` or ``` ... ```)
    # Try to find JSON in code fences
    fence_patterns = [r"```json\s*\n(.*?)\n```", r"```\s*\n(.*?)\n```", r"```json(.*?)```", r"```(.*?)```"]

    for pattern in fence_patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                return loads_lenient(match.strip())
            except Exception:
                continue

    # Try to extract JSON objects using JSONDecoder so braces inside strings do
    # not confuse extraction.  Return at the first valid match to bound
    # memory/CPU; previously this accumulated all matches before selecting.
    def extract_first_json_object(text):
        """Return the first valid JSON object found via raw_decode, or None."""
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(invalid_json_escape.sub(r"\\\\", text[index:]))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and ("criteria" in parsed or "pass" in parsed or "score" in parsed):
                return parsed
        return None

    structured = extract_first_json_object(text)
    if structured is not None:
        return structured

    # Last resort: scan for any valid JSON object (e.g. a plain object without
    # ``criteria``/``pass``/``score`` keys); return the first one.
    def extract_first_any_object(text):
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(invalid_json_escape.sub(r"\\\\", text[index:]))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    any_object = extract_first_any_object(text)
    if any_object is not None:
        return any_object

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
                return loads_lenient(match)
            except Exception:
                continue

    return text
