"""Unit tests for runtime manifest check ID resolution."""

from pathlib import Path
from unittest.mock import patch

from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.executor import TestExecutor
from nichebench.core.scoring import RuntimeScorer


def _make_executor(runtime_config=None):
    mut_cfg = {"provider": "groq", "model": "test-model", "parameters": {}}
    judge_cfg = {"provider": "openai", "model": "gpt-5", "parameters": {}}
    network_cfg = {"timeout": 30, "retry_attempts": 1, "retry_delay": 1}

    with (
        patch("nichebench.core.executor.get_config") as mock_config,
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
