"""Runtime scoring validation.

Owner: scoring package.
Boundary: validates runtime task manifests (``TestCaseSpec``) and container
image references before any runtime resources are allocated.

Why here instead of ``core/validation.py``
-----------------------------------------
Manifest validation must run early during ``TestCaseSpec`` construction,
before any runtime resources are allocated.  Placing it in ``core/validation.py``
would load it for every task type including static tasks, creating an
undesirable cross-module import cycle.

Public API
----------
ValidationError        — exception raised by the validators below.
validate_container_image_pin(image: str) -> None
    Raises ``ValidationError`` for floating/unpinned tags.
validate_runtime_testcase(tc: TestCaseSpec) -> None
    Validates a runtime test case manifest against the required schema.
"""

from typing import Any

# Floating tags that are NOT considered pinned references.
_FLOATING_TAGS = frozenset(
    {
        "latest",
        "edge",
        "stable",
        "dev",
        "main",
        "master",
        "nightly",
        "canary",
        "test",
        "debug",
        "release",
    }
)


class ValidationError(Exception):
    """Exception raised for validation errors in task manifests."""


def validate_container_image_pin(image: str) -> None:
    """Validate that *image* is a pinned container reference.

    Raises ``ValidationError`` when the reference is empty, uses a floating
    tag (``latest``, ``edge``, etc.), or lacks an explicit tag/digest.
    """
    if not image or not image.strip():
        raise ValidationError("runtime.runtime_container_image must be configured for cage mode")
    ref = image.strip()

    # Digest-based references (image@sha256:...) are always valid.
    if "@sha256:" in ref:
        return

    # Must contain a colon (tag separator).
    if ":" not in ref:
        raise ValidationError(
            f"runtime_container_image '{image}' is not pinned (no tag found). "
            "Use an explicit tag or a digest reference."
        )

    # Extract the tag: text after the last colon, before any @digest.
    tag = ref.rsplit(":", 1)[-1].split("@")[0].strip()

    if not tag:
        raise ValidationError(
            f"runtime_container_image '{image}' is not pinned (empty tag). "
            "Use an explicit tag or a digest reference."
        )

    if tag.lower() in _FLOATING_TAGS:
        raise ValidationError(
            f"runtime_container_image '{image}' uses floating tag '{tag}'. "
            f"Use an explicit version tag (not one of {', '.join(sorted(_FLOATING_TAGS))}) "
            "or a digest reference."
        )


def validate_runtime_testcase(tc: Any) -> None:
    """Validate a runtime test case against the required schema."""
    # Lazy import to avoid circular dependency at module load time.
    from nichebench.core.datamodel import TestCaseSpec

    if not isinstance(tc, TestCaseSpec):
        return
    if tc.type != "runtime":
        return

    # Required fields
    required_fields = ["source", "environment", "agent", "checks", "scoring", "deliverables"]
    missing = [f for f in required_fields if not tc.raw.get(f)]
    if missing:
        raise ValidationError(f"Runtime task {tc.id} missing required fields: {', '.join(missing)}")

    # Environment must be a dict
    env = tc.raw.get("environment", {})
    if not isinstance(env, dict):
        raise ValidationError(f"Runtime task {tc.id} 'environment' field must be a dictionary.")

    setup_mode = env.get("setup_mode")
    if setup_mode not in ["config_import", "db_snapshot"]:
        raise ValidationError(
            f"Runtime task {tc.id} has invalid setup_mode: {setup_mode}. " f"Must be 'config_import' or 'db_snapshot'."
        )

    # Source must be a dict with base_branch or task_branch
    source = tc.raw.get("source", {})
    if not isinstance(source, dict):
        raise ValidationError(f"Runtime task {tc.id} 'source' field must be a dictionary.")
    if not (source.get("base_branch") or source.get("task_branch")):
        raise ValidationError(f"Runtime task {tc.id} 'source' missing required field: base_branch/task_branch")

    # Browser artifacts must be a dict when provided
    browser_artifacts = tc.raw.get("browser_artifacts")
    if browser_artifacts is not None and not isinstance(browser_artifacts, dict):
        raise ValidationError(f"Runtime task {tc.id} 'browser_artifacts' field must be a dictionary when provided.")

    # Checks validation
    checks = tc.raw.get("checks", [])
    if isinstance(checks, list):
        for i, check in enumerate(checks):
            if not isinstance(check, dict) or "type" not in check:
                raise ValidationError(f"Runtime task {tc.id} check #{i} is missing 'type'.")
            check_type = check.get("type")
            valid_check_types = ["fail_to_pass", "pass_to_pass", "required_command", "path_policy"]
            if check_type not in valid_check_types:
                raise ValidationError(
                    f"Runtime task {tc.id} check #{i} has invalid type: {check_type}. "
                    f"Must be one of {', '.join(valid_check_types)}."
                )
    elif isinstance(checks, dict):
        allowed_keys = {"fail_to_pass", "pass_to_pass", "required_commands", "allowed_paths", "static"}
        unknown = set(checks.keys()) - allowed_keys
        if unknown:
            raise ValidationError(f"Runtime task {tc.id} checks dict has unknown keys: {', '.join(sorted(unknown))}")
    else:
        raise ValidationError(f"Runtime task {tc.id} 'checks' field must be a list or dictionary.")
