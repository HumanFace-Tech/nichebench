"""Tests for runtime pack discovery and loading.

Public CI checks out only the public repository, so the private runtime-pack
submodule (``src/nichebench/frameworks/drupal_runtime/data``) is not
guaranteed to be present. The tests below resolve all framework/manifest paths
relative to the installed package and skip the manifest-dependent tests with a
clear reason when the runtime-pack data is unavailable. Image-pin validation
tests have no submodule dependency and always run.
"""

from pathlib import Path

import pytest

import nichebench
from nichebench.core.discovery import discover_frameworks
from nichebench.core.framework_loader import load_testcase_from_file
from nichebench.execution.runtime.scoring import (
    validate_container_image_pin,
    validate_runtime_testcase,
)

# Resolve the frameworks directory relative to the installed ``nichebench``
# package, never a workspace-specific absolute path. This keeps the tests
# portable across checkouts (local devcontainer, CI, editable installs).
_FRAMEWORKS_DIR = Path(nichebench.__file__).resolve().parent / "frameworks"
_DRUPAL_RUNTIME_DATA = _FRAMEWORKS_DIR / "drupal_runtime" / "data"
_DRUPAL_RUNTIME_MANIFEST = _DRUPAL_RUNTIME_DATA / "tasks" / "manifest" / "drupal_runtime_001.yaml"
_DRUPAL_RUNTIME_MANIFEST_TWO = _DRUPAL_RUNTIME_DATA / "tasks" / "manifest" / "drupal_runtime_002.yaml"


def _runtime_pack_present(manifest: Path = _DRUPAL_RUNTIME_MANIFEST) -> bool:
    """Return ``True`` when the runtime-pack manifest at *manifest* exists."""
    return manifest.is_file()


# Evaluated once at collection time. Public CI without submodule credentials
# will see this as ``False`` and skip the manifest-dependent tests.
_RUNTIME_PACK_AVAILABLE = _runtime_pack_present()

requires_runtime_pack = pytest.mark.skipif(
    not _RUNTIME_PACK_AVAILABLE,
    reason=(
        f"Private runtime-pack data submodule not checked out "
        f"(missing {_DRUPAL_RUNTIME_MANIFEST}); initialize the submodule to run "
        f"these tests locally."
    ),
)


@requires_runtime_pack
def test_discover_drupal_runtime_as_single_runtime_category():
    frameworks = discover_frameworks(_FRAMEWORKS_DIR)

    assert "drupal_runtime" in frameworks
    assert [task.task_type for task in frameworks["drupal_runtime"]] == ["runtime"]
    assert sum(len(task.testcases) for task in frameworks["drupal_runtime"]) == 5


@requires_runtime_pack
def test_runtime_manifest_loader_uses_task_branch_and_prompt_composition():
    testcase = load_testcase_from_file(_DRUPAL_RUNTIME_MANIFEST)

    assert testcase.type == "runtime"
    assert testcase.id == "drupal_runtime_001"
    assert testcase.base_branch == "task/drupal_runtime_001"
    assert testcase.prompt is not None
    assert "Acceptance criteria:" in testcase.prompt
    assert testcase.context is not None
    assert "Technical hints:" in testcase.context


@requires_runtime_pack
def test_runtime_validation_accepts_task_branch_baseline():
    testcase = load_testcase_from_file(_DRUPAL_RUNTIME_MANIFEST_TWO)

    validate_runtime_testcase(testcase)


# --- Path-resolution self-tests (always runnable; prove path/skip logic) ---


def test_frameworks_dir_resolves_relative_to_package():
    """Frameworks directory must derive from the installed package, never a workspace path."""
    expected = Path(nichebench.__file__).resolve().parent / "frameworks"
    assert expected == _FRAMEWORKS_DIR
    assert _FRAMEWORKS_DIR.is_dir(), f"Expected {_FRAMEWORKS_DIR} to exist"
    # The frameworks directory's parent must be the nichebench package directory,
    # proving the path is constructed relative to the package rather than a
    # hardcoded absolute literal.
    assert _FRAMEWORKS_DIR.parent == Path(nichebench.__file__).resolve().parent


def test_runtime_pack_marker_tracks_manifest_presence():
    """Skip marker condition must match the actual runtime-pack manifest presence."""
    # Consistency: the module-level availability flag must match the real file.
    assert _DRUPAL_RUNTIME_MANIFEST.is_file() == _RUNTIME_PACK_AVAILABLE

    # The skipif marker must be configured with a clear, informative reason.
    skip_mark = requires_runtime_pack.mark
    assert skip_mark.name == "skipif"
    assert "Private runtime-pack data submodule not checked out" in skip_mark.kwargs["reason"]
    assert str(_DRUPAL_RUNTIME_MANIFEST) in skip_mark.kwargs["reason"]


def test_runtime_pack_helper_reports_missing_when_manifest_absent(tmp_path):
    """The pack-presence helper must report missing for any non-existent path.

    This proves the skip logic without deleting or modifying the real submodule.
    """
    absent = tmp_path / "drupal_runtime_001.yaml"
    assert _runtime_pack_present(absent) is False


def test_runtime_pack_helper_reports_present_for_real_manifest():
    """The pack-presence helper must report present when the real manifest is checked out."""
    assert _runtime_pack_present(_DRUPAL_RUNTIME_MANIFEST) is _RUNTIME_PACK_AVAILABLE


def test_runtime_pack_marker_skips_when_manifest_unavailable(tmp_path, monkeypatch):
    """End-to-end: rebuild the skip marker against a missing path; it must skip.

    This proves the skip logic without deleting or modifying the real submodule.
    """
    import tests.unit.core.test_runtime_loader as module

    absent = tmp_path / "drupal_runtime_001.yaml"
    # Simulate the public-CI condition: pack manifest is absent.
    monkeypatch.setattr(module, "_DRUPAL_RUNTIME_MANIFEST", absent)
    rebuilt_skip = pytest.mark.skipif(
        not module._runtime_pack_present(module._DRUPAL_RUNTIME_MANIFEST),
        reason=(
            f"Private runtime-pack data submodule not checked out "
            f"(missing {absent}); initialize the submodule to run "
            f"these tests locally."
        ),
    )
    # skipif condition must be True (skip) when the manifest is missing.
    assert rebuilt_skip.mark.args[0] is True
    assert str(absent) in rebuilt_skip.mark.kwargs["reason"]


# --- validate_container_image_pin tests (always runnable; no pack data needed) ---


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
