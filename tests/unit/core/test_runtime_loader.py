"""Tests for runtime pack discovery and loading."""

from pathlib import Path

import pytest

from nichebench.core.discovery import discover_frameworks
from nichebench.core.loader_yaml import load_testcase_from_file
from nichebench.core.validation import (
    validate_container_image_pin,
    validate_runtime_testcase,
)


def test_discover_drupal_runtime_as_single_runtime_category():
    frameworks = discover_frameworks(Path("/workspaces/nichebench/src/nichebench/frameworks"))

    assert "drupal_runtime" in frameworks
    assert [task.task_type for task in frameworks["drupal_runtime"]] == ["runtime"]
    assert sum(len(task.testcases) for task in frameworks["drupal_runtime"]) == 5


def test_runtime_manifest_loader_uses_task_branch_and_prompt_composition():
    testcase = load_testcase_from_file(
        Path(
            "/workspaces/nichebench/src/nichebench/frameworks/"
            "drupal_runtime/data/tasks/manifest/drupal_runtime_001.yaml"
        )
    )

    assert testcase.type == "runtime"
    assert testcase.id == "drupal_runtime_001"
    assert testcase.base_branch == "task/drupal_runtime_001"
    assert testcase.prompt is not None
    assert "Acceptance criteria:" in testcase.prompt
    assert testcase.context is not None
    assert "Technical hints:" in testcase.context


def test_runtime_validation_accepts_task_branch_baseline():
    testcase = load_testcase_from_file(
        Path(
            "/workspaces/nichebench/src/nichebench/frameworks/"
            "drupal_runtime/data/tasks/manifest/drupal_runtime_002.yaml"
        )
    )

    validate_runtime_testcase(testcase)


# --- validate_container_image_pin tests ---


def test_image_pin_rejects_empty():
    with pytest.raises(Exception, match="must be configured"):
        validate_container_image_pin("")


def test_image_pin_rejects_latest():
    with pytest.raises(Exception, match="floating tag"):
        validate_container_image_pin("ghcr.io/opencode-ai/opencode:latest")


def test_image_pin_accepts_explicit_tag():
    validate_container_image_pin("ghcr.io/opencode-ai/opencode:v2.3.1")


def test_image_pin_accepts_digest():
    validate_container_image_pin("ghcr.io/opencode-ai/opencode@sha256:abcdef1234567890")


# --- Extended floating-tag rejection tests (Task 1.2) ---


@pytest.mark.parametrize(
    "floating_tag",
    [
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
    ],
)
def test_image_pin_rejects_floating_tags(floating_tag):
    with pytest.raises(Exception, match="floating tag"):
        validate_container_image_pin(f"ghcr.io/opencode-ai/opencode:{floating_tag}")


def test_image_pin_rejects_bare_image_no_tag():
    with pytest.raises(Exception, match="no tag found"):
        validate_container_image_pin("ghcr.io/opencode-ai/opencode")


def test_image_pin_accepts_version_tag():
    validate_container_image_pin("ghcr.io/opencode-ai/opencode:v0.14.0")


def test_image_pin_accepts_semver_tag():
    validate_container_image_pin("ghcr.io/opencode-ai/opencode:1.2.3")


def test_image_pin_is_case_insensitive_for_floating_tags():
    with pytest.raises(Exception, match="floating tag"):
        validate_container_image_pin("ghcr.io/opencode-ai/opencode:Latest")
    with pytest.raises(Exception, match="floating tag"):
        validate_container_image_pin("ghcr.io/opencode-ai/opencode:EDGE")
