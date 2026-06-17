"""JSON extraction and normalisation for judge output.

This module wraps :func:`nichebench.providers.litellm_client.parse_json_safe`
with module-level documentation of the parsing contract.

Ownership
=========
This module is owned by the ``litellm_judge`` package. All JSON parsing
in the judge pipeline flows through here. Callers should not import
``parse_json_safe`` directly from ``litellm_client``.

Parsing contract
================
``parse_json_safe`` attempts to extract a valid JSON object from free-form
text using the following strategies (in order):

1. Direct ``json.loads`` if the text is a valid JSON object.
2. Extraction from code fences (`````json ...````` or ````` ...`````).
3. Brace-counting extraction that finds the first balanced ``{ ... }``
   object containing at least one of: ``criteria``, ``pass``, ``score``.
4. Regex fallback for common top-level keys.

If all strategies fail, the original text string is returned unchanged.
The caller is responsible for detecting this and applying a conservative
zero-score fallback.
"""

from nichebench.providers.litellm_client import parse_json_safe

__all__ = ["parse_json_safe"]
