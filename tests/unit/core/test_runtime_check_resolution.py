"""Unit tests for runtime manifest check ID resolution.

Public CI checks out only the public repository, so the private runtime-pack
data submodule (``src/nichebench/frameworks/drupal_runtime/data``) is not
guaranteed to be present. The pack-dependent test below skips with a clear
reason when the runtime-pack data is unavailable; every other test is
self-contained and always runs.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import nichebench
from nichebench.core.datamodel import TestCaseSpec
from nichebench.execution.orchestrator import TestExecutor
from nichebench.execution.runtime.scoring import RuntimeScorer
from nichebench.execution.runtime.scoring.datamodel import CheckResult


def _make_executor(runtime_config=None):
    mut_cfg = {"provider": "groq", "model": "test-model", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

    with (
        patch("nichebench.execution.orchestrator.get_config") as mock_config,
        patch.object(TestExecutor, "_load_system_prompt", return_value=None),
        patch.object(TestExecutor, "_load_judge_system_prompt", return_value=None),
    ):
        mock_config.return_value.get_evaluation_config.return_value = runtime_config or {}
        mock_config.return_value.get_model_string.side_effect = lambda cfg: f"{cfg['provider']}/{cfg['model']}"
        return TestExecutor(
            framework="drupal_runtime",
            category="runtime",
            mut_config=mut_cfg,
            judge_config=judge_cfg,
            network_config=network_cfg,
        )


def test_runtime_manifest_id_resolves_to_concrete_check_spec(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tasks" / "manifest" / "drupal_runtime_test.yaml"
    checks_path = tmp_path / "tasks" / "checks" / "drupal_runtime_test.yaml"
    manifest_path.parent.mkdir(parents=True)
    checks_path.parent.mkdir(parents=True)
    manifest_path.write_text("task_id: drupal_runtime_test\n", encoding="utf-8")
    checks_path.write_text(
        """
task_id: drupal_runtime_test
checks:
  - id: module_info_exists
    label: Module info file exists
    op: file_exists
    path: web/modules/custom/example/example.info.yml
""".strip()
        + "\n",
        encoding="utf-8",
    )

    test_case = TestCaseSpec(
        id="drupal_runtime_test",
        type="runtime",
        raw={"checks": {"fail_to_pass": ["module_info_exists"]}},
        file_path=str(manifest_path),
    )
    executor = _make_executor({"runtime_mode": "cage"})

    checks = executor._load_runtime_checks(test_case)

    assert len(checks) == 1
    assert checks[0]["id"] == "module_info_exists"
    assert checks[0]["op"] == "file_exists"
    assert checks[0]["path"] == "web/modules/custom/example/example.info.yml"
    assert checks[0]["category"] == "fail_to_pass"
    assert checks[0]["critical"] is True


def test_runtime_manifest_missing_id_becomes_explicit_unknown_check_failure(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tasks" / "manifest" / "drupal_runtime_test.yaml"
    checks_path = tmp_path / "tasks" / "checks" / "drupal_runtime_test.yaml"
    manifest_path.parent.mkdir(parents=True)
    checks_path.parent.mkdir(parents=True)
    manifest_path.write_text("task_id: drupal_runtime_test\n", encoding="utf-8")
    checks_path.write_text(
        """
task_id: drupal_runtime_test
checks:
  - id: known_check
    op: file_exists
    path: web/modules/custom/example/example.info.yml
""".strip()
        + "\n",
        encoding="utf-8",
    )

    test_case = TestCaseSpec(
        id="drupal_runtime_test",
        type="runtime",
        raw={"checks": {"fail_to_pass": ["missing_check"]}},
        file_path=str(manifest_path),
    )
    executor = _make_executor({"runtime_mode": "cage"})
    checks = executor._load_runtime_checks(test_case)

    assert len(checks) == 1
    assert checks[0]["type"] == "unknown_runtime_check_id"
    assert checks[0]["message"] == "Unknown runtime check id: missing_check"

    scorer = RuntimeScorer(workspace_path=tmp_path)
    with patch.object(RuntimeScorer, "_run_command", side_effect=AssertionError("must not execute shell commands")):
        results = scorer.run_deterministic_checks(checks)

    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].message == "Unknown runtime check id: missing_check"


def test_required_command_criticality_is_explicit_and_defaults_to_critical(tmp_path: Path) -> None:
    manifest = tmp_path / "tasks" / "manifest" / "example.yaml"
    sidecar = tmp_path / "tasks" / "checks" / "example.yaml"
    manifest.parent.mkdir(parents=True)
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        "checks:\n  - id: example\n    op: file_exists\n    path: example\n"
        'required_command_criticality:\n  "ddev drush cr": false\n  "bad": no\n',
        encoding="utf-8",
    )
    case = TestCaseSpec(
        id="example",
        type="runtime",
        file_path=str(manifest),
        raw={"checks": {"fail_to_pass": ["example"], "required_commands": ["ddev drush cr", "other"]}},
    )
    checks = _make_executor()._load_runtime_checks(case)
    assert [check["critical"] for check in checks] == [True, False, True]


def test_criticality_changes_gate_not_numeric_score(tmp_path: Path) -> None:
    scorer = RuntimeScorer(workspace_path=tmp_path)
    checks = [CheckResult("a", "test", True, ""), CheckResult("b", "test", False, "")]
    config = {"deterministic_weight": 0.5, "llm_weight": 0.5}
    critical = scorer.compute_hybrid_score(checks, judge_score=1.0, scoring_config=config)
    checks[1].is_critical = False
    noncritical = scorer.compute_hybrid_score(checks, judge_score=1.0, scoring_config=config)
    assert critical.deterministic_score == noncritical.deterministic_score
    assert critical.final_score == noncritical.final_score
    assert not critical.passed and noncritical.passed


def test_empty_sidecar_still_honors_required_command_criticality(tmp_path: Path) -> None:
    manifest = tmp_path / "tasks" / "manifest" / "example.yaml"
    sidecar = tmp_path / "tasks" / "checks" / "example.yaml"
    manifest.parent.mkdir(parents=True)
    sidecar.parent.mkdir(parents=True)
    manifest.write_text("task_id: example\n", encoding="utf-8")
    # Checks list is empty, but a criticality override is still declared.
    sidecar.write_text(
        "task_id: example\n" "required_command_criticality:\n" '  "ddev drush cr": false\n',
        encoding="utf-8",
    )
    case = TestCaseSpec(
        id="example",
        type="runtime",
        file_path=str(manifest),
        raw={"checks": {"required_commands": ["ddev drush cr", "other"]}},
    )
    checks = _make_executor()._load_runtime_checks(case)
    by_command = {c["command"]: c["critical"] for c in checks if c["type"] == "required_command"}
    assert by_command == {"ddev drush cr": False, "other": True}


def test_empty_sidecar_absent_or_invalid_criticality_defaults_to_critical(tmp_path: Path) -> None:
    manifest = tmp_path / "tasks" / "manifest" / "example.yaml"
    sidecar = tmp_path / "tasks" / "checks" / "example.yaml"
    manifest.parent.mkdir(parents=True)
    sidecar.parent.mkdir(parents=True)
    manifest.write_text("task_id: example\n", encoding="utf-8")
    sidecar.write_text(
        "task_id: example\n"
        "required_command_criticality:\n"
        '  "ddev drush cr": 1\n'
        '  "bad": "false"\n'
        '  "truthy": "true"\n',
        encoding="utf-8",
    )
    case = TestCaseSpec(
        id="example",
        type="runtime",
        file_path=str(manifest),
        raw={"checks": {"required_commands": ["ddev drush cr", "bad", "truthy", "absent"]}},
    )
    checks = _make_executor()._load_runtime_checks(case)
    by_command = {c["command"]: c["critical"] for c in checks if c["type"] == "required_command"}
    # Absent key ("absent") and non-boolean overrides all fall back to critical.
    assert by_command == {"ddev drush cr": True, "bad": True, "truthy": True, "absent": True}


# Resolve the pack directories relative to the installed ``nichebench`` package,
# never a workspace-specific absolute path. This keeps the tests portable
# across checkouts (local devcontainer, CI, editable installs).
_PACK_DATA_DIR = Path(nichebench.__file__).resolve().parent / "frameworks" / "drupal_runtime" / "data"
_PACK_TASKS_DIR = _PACK_DATA_DIR / "tasks"
_PACK_MANIFEST_DIR = _PACK_TASKS_DIR / "manifest"
_PACK_CHECKS_DIR = _PACK_TASKS_DIR / "checks"
_PACK_PROBE_MANIFEST = _PACK_MANIFEST_DIR / "drupal_runtime_001.yaml"

# Task ids the runtime pack is expected to ship. The pack test asserts that
# every one of them is discovered, so a shrinking pack fails loudly instead of
# silently validating fewer tasks.
_PACK_TASK_IDS = (
    "drupal_runtime_001",
    "drupal_runtime_002",
    "drupal_runtime_003",
    "drupal_runtime_004",
    "drupal_runtime_005",
)


def _runtime_pack_present(manifest: Path = _PACK_PROBE_MANIFEST) -> bool:
    """Return ``True`` when the runtime-pack manifest at *manifest* exists."""
    return manifest.is_file()


# Evaluated once at collection time. Public CI without submodule credentials
# will see this as ``False`` and skip the pack-dependent test.
_RUNTIME_PACK_AVAILABLE = _runtime_pack_present()


def _pack_skip_marker(manifest: Path = _PACK_PROBE_MANIFEST) -> pytest.MarkDecorator:
    """Build the skip marker used to guard the runtime-pack test."""
    return pytest.mark.skipif(
        not _runtime_pack_present(manifest),
        reason=(
            f"Private runtime-pack data submodule not checked out "
            f"(missing {manifest}); initialize the submodule to run "
            f"these tests locally."
        ),
    )


requires_runtime_pack = _pack_skip_marker()


def _discover_pack_task_ids() -> tuple[str, ...]:
    """Return every task id present in the pack's manifest and checks dirs.

    Both directories are scanned so a sidecar without a manifest (or the
    reverse) is still discovered and reported by the pack test.
    """
    task_ids: set[str] = set()
    for directory in (_PACK_MANIFEST_DIR, _PACK_CHECKS_DIR):
        if not directory.is_dir():
            continue
        task_ids.update(path.stem for path in directory.glob("drupal_runtime_*.yaml"))
    return tuple(sorted(task_ids))


@requires_runtime_pack
def test_pack_required_command_criticality_keys_match_manifest() -> None:
    task_ids = _discover_pack_task_ids()
    assert task_ids, f"no runtime-pack tasks discovered under {_PACK_TASKS_DIR}"
    missing = [task_id for task_id in _PACK_TASK_IDS if task_id not in task_ids]
    assert not missing, f"runtime-pack is missing expected tasks: {missing}"

    for task_id in task_ids:
        sidecar = _PACK_CHECKS_DIR / f"{task_id}.yaml"
        manifest = _PACK_MANIFEST_DIR / f"{task_id}.yaml"
        assert sidecar.is_file(), f"missing sidecar for {task_id}"
        assert manifest.is_file(), f"missing manifest for {task_id}"
        parsed = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        manifest_data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        required_commands = (
            manifest_data.get("checks", {}).get("required_commands", []) if isinstance(manifest_data, dict) else []
        )
        overrides = parsed.get("required_command_criticality", {}) if isinstance(parsed, dict) else {}
        assert set(overrides) == set(
            required_commands
        ), f"required_command_criticality keys for {task_id} do not match manifest required_commands"


# --- Pack-availability self-tests (always runnable; prove path/skip logic) ---


def test_pack_paths_resolve_relative_to_package() -> None:
    """Pack paths must derive from the installed package, never a workspace path."""
    package_dir = Path(nichebench.__file__).resolve().parent
    expected = package_dir / "frameworks" / "drupal_runtime" / "data"
    assert expected == _PACK_DATA_DIR
    assert expected / "tasks" / "manifest" == _PACK_MANIFEST_DIR
    assert expected / "tasks" / "checks" == _PACK_CHECKS_DIR
    # The data dir's parent must be the framework directory inside the
    # installed package, proving the path is built relative to the package
    # rather than from a hardcoded absolute literal.
    assert _PACK_DATA_DIR.parent == package_dir / "frameworks" / "drupal_runtime"


def test_pack_marker_tracks_pack_presence() -> None:
    """Skip marker condition must match the actual runtime-pack presence."""
    assert _runtime_pack_present(_PACK_PROBE_MANIFEST) is _RUNTIME_PACK_AVAILABLE

    skip_mark = requires_runtime_pack.mark
    assert skip_mark.name == "skipif"
    assert skip_mark.args[0] is (not _RUNTIME_PACK_AVAILABLE)
    assert "Private runtime-pack data submodule not checked out" in skip_mark.kwargs["reason"]
    assert str(_PACK_PROBE_MANIFEST) in skip_mark.kwargs["reason"]


def test_pack_presence_helper_reports_missing_when_manifest_absent(tmp_path: Path) -> None:
    """The pack-presence helper must report missing for any non-existent path.

    This proves the skip logic without deleting or modifying the real submodule.
    """
    absent = tmp_path / "drupal_runtime_001.yaml"
    assert _runtime_pack_present(absent) is False


def test_pack_skip_marker_skips_when_pack_absent(tmp_path: Path) -> None:
    """End-to-end: rebuild the skip marker against a missing path; it must skip.

    This proves the skip logic without deleting or modifying the real submodule.
    """
    absent = tmp_path / "drupal_runtime_001.yaml"
    marker = _pack_skip_marker(absent)
    assert marker.mark.name == "skipif"
    assert marker.mark.args[0] is True
    assert str(absent) in marker.mark.kwargs["reason"]
