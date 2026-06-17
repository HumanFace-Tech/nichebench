"""Runtime scoring: scorer orchestration.

Owner: scoring package.
Boundary: ``RuntimeScorer`` is the main orchestrator for the scoring pipeline.
It exposes ``run_deterministic_checks`` (which calls ``check_runner.run_check``
for each op-based check) and ``compute_hybrid_score`` (which blends
deterministic + judge scores).

Scoring model
-------------
``RuntimeScorer`` executes deterministic checks against the live workspace
and combines them with an optional LLM judge score:

  final = (deterministic * deterministic_weight) + (judge * judge_weight)

Critical check failure (is_critical=True) gates the final score regardless
of magnitude — if any critical check fails, ``passed=False``.

Check execution boundaries
-------------------------
- Checks run inside the DDEV container via ``ddev exec`` (string commands)
  or directly (list commands already built).
- Checks that need the workspace use ``self.workspace_path`` as CWD.
- Checks that need Drush use ``self._drush_cmd`` (auto-detected as
  ``ddev drush`` or plain ``drush``).
- Static analysis helpers (``phpstan_clean``, ``composer_script_clean``)
  always go through ``ddev composer`` or ``ddev exec`` — they do not assume
  the host has phpstan or composer installed.
"""

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from nichebench.execution.runtime.scoring.datamodel import CheckResult, HybridScore

from . import check_runner


class RuntimeScorer:
    """Handles hybrid scoring for runtime tasks.

    ``run_deterministic_checks`` executes manifest-defined checks and returns
    a list of ``CheckResult``.  ``compute_hybrid_score`` then blends the
    deterministic result with an optional LLM judge score using per-manifest
    weights (default: 50 % deterministic / 50 % judge).
    """

    def __init__(
        self,
        workspace_path: Path,
        command_log: Optional[List[Dict[str, Any]]] = None,
        drush_cmd: Optional[List[str]] = None,
        command_timeout_seconds: int = 1800,
        run_log_path: Optional[Path] = None,
    ):
        self.workspace_path = workspace_path
        self.command_log = command_log or []
        self.command_timeout_seconds = command_timeout_seconds
        self._drush_cmd: Optional[List[str]] = drush_cmd or self._detect_drush()
        self.run_log_path = run_log_path

    @staticmethod
    def _detect_drush() -> Optional[List[str]]:
        """Return the appropriate drush invocation if DDEV or drush is available."""
        try:
            subprocess.run(["ddev", "--version"], capture_output=True, check=True)
            return ["ddev", "drush"]
        except (subprocess.CalledProcessError, FileNotFoundError):
            try:
                subprocess.run(["drush", "--version"], capture_output=True, check=True)
                return ["drush"]
            except (subprocess.CalledProcessError, FileNotFoundError):
                return None

    def run_deterministic_checks(self, checks_config: List[Dict[str, Any]]) -> List[CheckResult]:
        """Run a set of deterministic checks."""
        results = []
        for check in checks_config:
            if "op" in check:
                results.append(
                    check_runner.run_check(
                        op=str(check.get("op", "")),
                        spec=check,
                        workspace_path=self.workspace_path,
                        drush_cmd=self._drush_cmd,
                        command_timeout_seconds=self.command_timeout_seconds,
                    )
                )
                continue

            name = check.get("name", "Unnamed check")
            check_type = check.get("type")
            is_critical = check.get("critical", True)

            if check_type in ("fail_to_pass", "pass_to_pass", "static"):
                cmd = str(check.get("command") or "")
                if not cmd:
                    results.append(
                        CheckResult(
                            name,
                            str(check_type or "unknown"),
                            False,
                            "Missing command",
                            is_critical,
                            details={"command": cmd},
                        )
                    )
                    continue

                res = self._run_command(cmd)
                results.append(
                    CheckResult(
                        name,
                        str(check_type or "unknown"),
                        res["passed"],
                        res["message"],
                        is_critical,
                        details={
                            "command": cmd,
                            "stdout": res.get("stdout", ""),
                            "stderr": res.get("stderr", ""),
                            "returncode": res.get("returncode"),
                        },
                    )
                )

            elif check_type == "required_command":
                expected = str(check.get("command") or "")
                if not expected.strip():
                    results.append(
                        CheckResult(
                            name,
                            str(check_type or "required_command"),
                            False,
                            "Required command is blank or missing",
                            is_critical,
                        )
                    )
                    continue
                matched = any(
                    self._required_command_matches(expected, str(entry.get("command", "")))
                    for entry in self.command_log
                )
                # Fallback: if command_log is empty, search actual bash command
                # lines in run.log. Do not match assistant summaries like
                # "Ran: ddev drush cex --yes" as executed commands.
                if not matched and not self.command_log and self.run_log_path and self.run_log_path.exists():
                    try:
                        run_log_text = self.run_log_path.read_text(encoding="utf-8", errors="replace")
                        matched = any(
                            self._required_command_matches(expected, command)
                            for command in self._extract_run_log_commands(run_log_text)
                        )
                    except OSError:
                        pass
                results.append(
                    CheckResult(
                        name,
                        str(check_type or "required_command"),
                        matched,
                        "Command observed" if matched else f"Required command not observed: {expected}",
                        is_critical,
                    )
                )

            elif check_type == "path_policy":
                # Check that changes are only within allowed paths
                allowed_paths = check.get("allowed_paths", [])
                res = self._check_path_policy(allowed_paths)
                results.append(
                    CheckResult(name, str(check_type or "path_policy"), res["passed"], res["message"], is_critical)
                )

            elif check_type == "unknown_runtime_check_id":
                results.append(
                    CheckResult(
                        name,
                        str(check_type),
                        False,
                        str(check.get("message") or f"Unknown runtime check id: {check.get('id', name)}"),
                        is_critical,
                    )
                )

            else:
                results.append(
                    CheckResult(
                        name,
                        str(check_type or "unknown"),
                        False,
                        f"Unknown check type: {check_type}",
                        is_critical,
                    )
                )

        return results

    @staticmethod
    def _normalize_command_for_match(command: str) -> str:
        """Normalize command text for required_command checks."""
        text = RuntimeScorer._strip_ansi(command)
        text = re.sub(r"\s+", " ", text.strip())

        # Strip leading 'export ... &&' prefix.
        text = re.sub(r"^export\s+[^&]+&&\s*", "", text)

        # Strip one-or-more leading env assignments (KEY=VAL).
        while True:
            stripped = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*=[^\s]+\s+", "", text)
            if stripped == text:
                break
            text = stripped

        return text.strip()

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Strip ANSI escape sequences from OpenCode run logs."""
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)

    @classmethod
    def _extract_run_log_commands(cls, run_log_text: str) -> List[str]:
        """Extract shell commands actually executed by OpenCode from run.log."""
        commands: List[str] = []
        for raw_line in run_log_text.splitlines():
            line = cls._strip_ansi(raw_line).strip()
            if not line.startswith("$ "):
                continue
            command = line[2:].strip()
            if command:
                commands.append(command)
        return commands

    @classmethod
    def _required_command_matches(cls, expected: str, observed: str) -> bool:
        """Match required commands with optional wildcard support."""
        expected = expected.strip()
        observed_raw = observed.strip()
        observed_normalized = cls._normalize_command_for_match(observed_raw)

        has_wildcard = any(ch in expected for ch in "*?[]")
        if has_wildcard:
            return fnmatch.fnmatch(observed_normalized, expected)

        return expected in observed_raw or expected in observed_normalized

    @staticmethod
    def normalize_checks(raw_checks: Any) -> List[Dict[str, Any]]:
        """Normalize checks config from dict/list into list of typed checks."""
        if isinstance(raw_checks, list):
            return raw_checks
        if not isinstance(raw_checks, dict):
            return []

        normalized: List[Dict[str, Any]] = []
        for cmd in raw_checks.get("fail_to_pass", []):
            normalized.append({"name": str(cmd), "type": "fail_to_pass", "command": str(cmd), "critical": True})
        for cmd in raw_checks.get("pass_to_pass", []):
            normalized.append({"name": str(cmd), "type": "pass_to_pass", "command": str(cmd), "critical": True})
        for cmd in raw_checks.get("required_commands", []):
            normalized.append({"name": str(cmd), "type": "required_command", "command": str(cmd), "critical": True})
        for cmd in raw_checks.get("static", []):
            normalized.append({"name": str(cmd), "type": "static", "command": str(cmd), "critical": True})
        if raw_checks.get("allowed_paths"):
            normalized.append(
                {
                    "name": "path_policy",
                    "type": "path_policy",
                    "allowed_paths": raw_checks.get("allowed_paths", []),
                    "critical": True,
                }
            )
        return normalized

    def _run_command(self, cmd: Any) -> Dict[str, Any]:
        """Run a command and return stdout/stderr plus pass/fail metadata."""
        try:
            run_cmd = ["ddev", "exec", "--", cmd] if isinstance(cmd, str) else cmd
            result = subprocess.run(
                run_cmd,
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=max(self.command_timeout_seconds, 1),
            )
            return {
                "passed": True,
                "message": result.stdout or "Command passed",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.CalledProcessError as e:
            return {
                "passed": False,
                "message": f"Command failed: {e.stderr or e.stdout}",
                "stdout": e.stdout,
                "stderr": e.stderr,
                "returncode": e.returncode,
            }
        except FileNotFoundError:
            return {
                "passed": False,
                "message": "ddev command not found",
                "stdout": "",
                "stderr": "ddev command not found",
                "returncode": 127,
            }

    def _check_path_policy(self, allowed_paths: List[str]) -> Dict[str, Any]:
        """Verify that only allowed paths were modified."""
        if not allowed_paths:
            return {"passed": True, "message": "No path restrictions"}

        try:
            # Check changed files using git
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=max(self.command_timeout_seconds, 1),
            )
            changed_files = result.stdout.splitlines()

            violations = []
            for f in changed_files:
                is_allowed = False
                for allowed in allowed_paths:
                    if f.startswith(allowed):
                        is_allowed = True
                        break
                if not is_allowed:
                    violations.append(f)

            if violations:
                return {"passed": False, "message": f"Violations: {', '.join(violations)}"}
            return {"passed": True, "message": "Path policy compliant"}

        except subprocess.CalledProcessError as e:
            return {"passed": False, "message": f"Failed to check path policy: {e.stderr}"}

    def compute_hybrid_score(
        self,
        check_results: List[CheckResult],
        judge_score: Optional[float] = None,
        scoring_config: Optional[Dict[str, Any]] = None,
    ) -> HybridScore:
        """Compute the final hybrid score.

        Blends deterministic and judge scores using per-manifest weights.
        Critical check failure gates the final score regardless of magnitude.
        """
        scoring_config = scoring_config or {}
        deterministic_weight = scoring_config.get("deterministic_weight", 0.7)
        # Manifests use the key "llm_weight"; fall back to "judge_weight" for
        # legacy configs, then to the hardcoded default of 0.3.
        judge_weight = scoring_config.get("llm_weight", scoring_config.get("judge_weight", 0.3))

        # 1. Calculate deterministic score
        total_checks = len(check_results)
        if total_checks > 0:
            passed_checks = sum(1 for r in check_results if r.passed)
            deterministic_score = passed_checks / total_checks
        else:
            deterministic_score = 1.0  # No checks means it passed by default if it ran

        # 2. Check for critical failures
        critical_fail = any(r.is_critical and not r.passed for r in check_results)

        # 3. Hybrid composition
        if judge_score is not None and judge_weight > 0:
            final_score = (deterministic_score * deterministic_weight) + (judge_score * judge_weight)
        else:
            # Deterministic-only fallback
            final_score = deterministic_score

        # If any critical check failed, passed is False regardless of score
        passed = not critical_fail and final_score >= scoring_config.get("threshold", 0.7)

        return HybridScore(
            deterministic_score=deterministic_score,
            judge_score=judge_score,
            final_score=final_score,
            check_results=check_results,
            passed=passed,
        )
