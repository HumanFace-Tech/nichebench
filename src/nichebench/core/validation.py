from nichebench.core.datamodel import TestCaseSpec


class ValidationError(Exception):
    """Exception raised for validation errors in task manifests."""

    pass


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


def validate_runtime_testcase(tc: TestCaseSpec):
    """Validate a runtime test case against the required schema."""
    if tc.type != "runtime":
        return

    # 1.1 Add task_type: runtime schema support with required fields
    required_fields = ["source", "environment", "agent", "checks", "scoring", "deliverables"]
    missing = [f for f in required_fields if not tc.raw.get(f)]

    # 1.2 Implement manifest validation errors for missing/invalid runtime fields
    if missing:
        raise ValidationError(f"Runtime task {tc.id} missing required fields: {', '.join(missing)}")

    # 1.3 Add setup mode validation for config_import and db_snapshot
    env = tc.raw.get("environment", {})
    if not isinstance(env, dict):
        raise ValidationError(f"Runtime task {tc.id} 'environment' field must be a dictionary.")

    setup_mode = env.get("setup_mode")
    if setup_mode not in ["config_import", "db_snapshot"]:
        raise ValidationError(
            f"Runtime task {tc.id} has invalid setup_mode: {setup_mode}. " f"Must be 'config_import' or 'db_snapshot'."
        )

    # Validate source fields for branch-based baselines (Task 2.1)
    source = tc.raw.get("source", {})
    if not isinstance(source, dict):
        raise ValidationError(f"Runtime task {tc.id} 'source' field must be a dictionary.")

    if not (source.get("base_branch") or source.get("task_branch")):
        raise ValidationError(f"Runtime task {tc.id} 'source' missing required field: base_branch/task_branch")

    browser_artifacts = tc.raw.get("browser_artifacts")
    if browser_artifacts is not None and not isinstance(browser_artifacts, dict):
        raise ValidationError(f"Runtime task {tc.id} 'browser_artifacts' field must be a dictionary when provided.")

    # Validate checks (Task 4.1)
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
