import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CheckResult:
    """Represents the result of a single deterministic check."""

    name: str
    type: str
    passed: bool
    message: str
    is_critical: bool = True
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HybridScore:
    """Represents a composite score from deterministic and judge components."""

    deterministic_score: float
    judge_score: Optional[float] = None
    final_score: float = 0.0
    check_results: List[CheckResult] = field(default_factory=list)
    passed: bool = False


class RuntimeScorer:
    """Handles hybrid scoring for runtime tasks (Task 4.1-4.4)."""

    def __init__(
        self,
        workspace_path: Path,
        command_log: Optional[List[Dict[str, Any]]] = None,
        drush_cmd: Optional[List[str]] = None,
        command_timeout_seconds: int = 1800,
    ):
        self.workspace_path = workspace_path
        self.command_log = command_log or []
        self.command_timeout_seconds = command_timeout_seconds
        self._drush_cmd: Optional[List[str]] = drush_cmd or self._detect_drush()

    @staticmethod
    def _detect_drush() -> Optional[List[str]]:
        try:
            subprocess.run(["ddev", "--version"], capture_output=True, check=True)
            return ["ddev", "drush"]
        except (subprocess.CalledProcessError, FileNotFoundError):
            try:
                subprocess.run(["drush", "--version"], capture_output=True, check=True)
                return ["drush"]
            except (subprocess.CalledProcessError, FileNotFoundError):
                return None

    @staticmethod
    def _timeout_value(seconds: int) -> int:
        return max(seconds, 1)

    def run_deterministic_checks(self, checks_config: List[Dict[str, Any]]) -> List[CheckResult]:
        """Run a set of deterministic checks (Task 4.1)."""
        results = []
        for check in checks_config:
            if "op" in check:
                results.append(self._run_runtime_check(check))
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
                matched = any(expected in str(entry.get("command", "")) for entry in self.command_log)
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

    def _run_runtime_check(self, check: Dict[str, Any]) -> CheckResult:
        name = str(check.get("label") or check.get("name") or check.get("id") or check.get("op") or "Unnamed check")
        op = str(check.get("op") or "")
        category = str(check.get("category") or "")
        critical = check.get("critical")
        is_critical = bool(category in {"fail_to_pass", "pass_to_pass"} if critical is None else critical)

        handlers = {
            "file_exists": self._op_file_exists,
            "file_glob_exists": self._op_file_glob_exists,
            "grep_file": self._op_grep_file,
            "grep_file_multi": self._op_grep_file_multi,
            "grep_dir": self._op_grep_dir,
            "grep_dir_count": self._op_grep_dir_count,
            "drush_output_contains": self._op_drush_output_contains,
            "drush_status_field": self._op_drush_status_field,
            "drush_watchdog_clean": self._op_drush_watchdog_clean,
            "drush_config_status_clean": self._op_drush_config_status_clean,
            "drush_pm_enabled": self._op_drush_pm_enabled,
            "composer_script_clean": self._op_composer_script_clean,
            "phpstan_clean": self._op_phpstan_clean,
            "routing_yml_contains": self._op_routing_yml_contains,
        }

        handler = handlers.get(op)
        if not handler:
            return CheckResult(
                name=name,
                type=op or "unknown",
                passed=False,
                message=f"Unknown operation: {op}",
                is_critical=is_critical,
            )

        outcome = handler(check)
        if isinstance(outcome, tuple) and len(outcome) == 3:
            raw_passed, raw_message, raw_details = outcome
            passed = bool(raw_passed)
            message = str(raw_message)
            details = raw_details if isinstance(raw_details, dict) else {}
        else:
            raw_passed, raw_message = outcome
            passed = bool(raw_passed)
            message = str(raw_message)
            details = {}
        return CheckResult(name=name, type=op, passed=passed, message=message, is_critical=is_critical, details=details)

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

    def _rglob_multi(self, directory: Path, include: str):
        brace_match = re.match(r"^(.*)\{([^}]+)\}(.*)$", include)
        if brace_match:
            prefix, alternatives, suffix = brace_match.groups()
            patterns = [f"{prefix}{alt.strip()}{suffix}" for alt in alternatives.split(",")]
        else:
            patterns = [include]

        seen = set()
        for pat in patterns:
            for path in directory.rglob(pat):
                if path not in seen:
                    seen.add(path)
                    yield path

    def _op_file_exists(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        path = self.workspace_path / str(spec["path"])
        if path.exists():
            return True, f"File exists: {spec['path']}"
        return False, f"File not found: {spec['path']}"

    def _op_file_glob_exists(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        directory = self.workspace_path / str(spec["dir"])
        pattern = str(spec["pattern"])
        if not directory.exists():
            return False, f"Directory not found: {spec['dir']}"

        matches = list(directory.glob(pattern))
        if matches:
            return True, f"Found {len(matches)} files matching {pattern} in {spec['dir']}"
        return False, f"No files matching {pattern} in {spec['dir']}"

    def _op_grep_file(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        path = self.workspace_path / str(spec["path"])
        pattern = str(spec["pattern"])
        if not path.exists():
            return False, f"File not found: {spec['path']}"

        content = path.read_text(encoding="utf-8")
        if re.search(pattern, content):
            return True, f"Pattern '{pattern}' found in {spec['path']}"
        return False, f"Pattern '{pattern}' not found in {spec['path']}"

    def _op_grep_file_multi(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        path = self.workspace_path / str(spec["path"])
        patterns = spec.get("patterns", [])
        if not path.exists():
            return False, f"File not found: {spec['path']}"

        content = path.read_text(encoding="utf-8")
        missing = [pattern for pattern in patterns if not re.search(str(pattern), content)]
        if not missing:
            return True, f"All {len(patterns)} patterns found in {spec['path']}"
        return False, f"Missing patterns in {spec['path']}: {', '.join(map(str, missing))}"

    def _op_grep_dir(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        directory = self.workspace_path / str(spec["dir"])
        pattern = str(spec["pattern"])
        include = str(spec.get("include", "**/*"))
        flags = spec.get("flags", [])

        if not directory.exists():
            return False, f"Directory not found: {spec['dir']}"

        re_flags = re.IGNORECASE if "case_insensitive" in flags else 0
        regex = re.compile(pattern, re_flags)

        for path in self._rglob_multi(directory, include):
            if path.is_file():
                try:
                    if regex.search(path.read_text(encoding="utf-8")):
                        return True, f"Pattern found in {path.relative_to(self.workspace_path)}"
                except Exception:
                    continue

        return False, f"Pattern '{pattern}' not found in {spec['dir']} matching {include}"

    def _op_grep_dir_count(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        directory = self.workspace_path / str(spec["dir"])
        pattern = str(spec["pattern"])
        include = str(spec.get("include", "**/*"))
        min_count = int(spec.get("min", 1))

        if not directory.exists():
            return False, f"Directory not found: {spec['dir']}"

        regex = re.compile(pattern)
        count = 0
        for path in self._rglob_multi(directory, include):
            if path.is_file():
                try:
                    if regex.search(path.read_text(encoding="utf-8")):
                        count += 1
                except Exception:
                    continue

        if count >= min_count:
            return True, f"Found {count} files (min {min_count}) matching pattern in {spec['dir']}"
        return False, f"Found only {count} files (min {min_count}) matching pattern in {spec['dir']}"

    def _op_drush_output_contains(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        if not self._drush_cmd:
            return False, "Drush/DDEV not available"

        cmd = self._drush_cmd + shlex.split(str(spec["command"]))
        result = self._run_command(cmd)
        combined = str(result.get("stdout", "")) + str(result.get("stderr", ""))
        pattern = str(spec["contains"])
        if re.search(pattern, combined):
            return True, f"Output matches '{pattern}'"
        return False, f"Output does not match '{pattern}'. Output: {combined[:200]}..."

    def _op_drush_status_field(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        if not self._drush_cmd:
            return False, "Drush/DDEV not available"

        field = str(spec["field"])
        matches = str(spec["matches"])
        cmd = self._drush_cmd + ["core:status", f"--field={field}"]
        result = self._run_command(cmd)
        value = str(result.get("stdout", "")).strip()
        if re.search(matches, value):
            return True, f"Field '{field}' value '{value}' matches '{matches}'"
        return False, f"Field '{field}' value '{value}' does not match '{matches}'"

    def _op_drush_watchdog_clean(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        if not self._drush_cmd:
            return False, "Drush/DDEV not available"

        cmd = self._drush_cmd + ["watchdog:show", "--count=50", "--severity=3", "--type=php"]
        result = self._run_command(cmd)
        combined = str(result.get("stdout", "")) + str(result.get("stderr", ""))
        if re.search(r"error|fatal|exception", combined, re.IGNORECASE):
            return False, f"PHP errors found in watchdog: {combined[:200]}..."
        return True, "No PHP errors in watchdog"

    def _op_drush_config_status_clean(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        if not self._drush_cmd:
            return False, "Drush/DDEV not available"

        cmd = self._drush_cmd + ["config:status"]
        result = self._run_command(cmd)
        combined = str(result.get("stdout", "")) + str(result.get("stderr", ""))
        if not combined.strip() or "No differences" in combined:
            return True, "Config is in sync"
        return False, "Config is out of sync"

    def _op_drush_pm_enabled(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        if not self._drush_cmd:
            return False, "Drush/DDEV not available"

        module = str(spec["module"])
        cmd = self._drush_cmd + ["pm:list", "--status=enabled", f"--filter={module}", "--format=json"]
        result = self._run_command(cmd)
        if module in str(result.get("stdout", "")):
            return True, f"Module '{module}' is enabled"
        return False, f"Module '{module}' is not enabled"

    def _op_composer_script_clean(self, spec: Dict[str, Any]) -> tuple[bool, str, Dict[str, Any]]:
        if not shutil.which("ddev"):
            return (
                False,
                "ddev not available",
                {
                    "command": "ddev composer",
                    "stdout": "",
                    "stderr": "ddev not available",
                    "returncode": 127,
                },
            )

        script = str(spec["script"])
        args = [str(arg) for arg in spec.get("args", [])]
        cmd = ["ddev", "composer", script] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_value(self.command_timeout_seconds),
                cwd=self.workspace_path,
            )
            if result.returncode == 0:
                return (
                    True,
                    f"Composer script '{script}' ran successfully",
                    {
                        "command": " ".join(cmd),
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "returncode": result.returncode,
                    },
                )
            combined = (result.stdout + result.stderr).strip()
            return (
                False,
                f"Composer script '{script}' failed: {combined[:300]}",
                {
                    "command": " ".join(cmd),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                },
            )
        except subprocess.TimeoutExpired:
            return (
                False,
                f"Composer script '{script}' timed out after {self.command_timeout_seconds}s",
                {
                    "command": " ".join(cmd),
                    "stdout": "",
                    "stderr": "timeout",
                    "returncode": None,
                },
            )

    def _op_phpstan_clean(self, spec: Dict[str, Any]) -> tuple[bool, str, Dict[str, Any]]:
        if not shutil.which("ddev"):
            return (
                False,
                "ddev not available",
                {
                    "command": "ddev composer stan",
                    "stdout": "",
                    "stderr": "ddev not available",
                    "returncode": 127,
                },
            )

        args = [str(arg) for arg in spec.get("args", [])]
        cmd = ["ddev", "composer", "stan"] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_value(self.command_timeout_seconds),
                cwd=self.workspace_path,
            )
            if result.returncode == 0:
                return (
                    True,
                    "PHPStan clean",
                    {
                        "command": " ".join(cmd),
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "returncode": result.returncode,
                    },
                )
            combined = (result.stdout + result.stderr).strip()
            return (
                False,
                f"PHPStan failed: {combined[:300]}",
                {
                    "command": " ".join(cmd),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                },
            )
        except subprocess.TimeoutExpired:
            return (
                False,
                f"PHPStan timed out after {self.command_timeout_seconds}s",
                {
                    "command": " ".join(cmd),
                    "stdout": "",
                    "stderr": "timeout",
                    "returncode": None,
                },
            )

    def _op_routing_yml_contains(self, spec: Dict[str, Any]) -> tuple[bool, str]:
        directory = self.workspace_path / "web/modules/custom"
        path_pattern = str(spec["path_pattern"])
        has_key = str(spec["has_key"])

        if not directory.exists():
            return False, "web/modules/custom not found"

        path_regex = re.compile(rf"path:.*({path_pattern})")
        key_regex = re.compile(has_key)

        for path in directory.glob("**/*.routing.yml"):
            content = path.read_text(encoding="utf-8")
            if (path_regex.search(content) or re.search(path_pattern, content)) and key_regex.search(content):
                return True, f"Route matching patterns found in {path.relative_to(self.workspace_path)}"

        return False, f"No routing.yml found with path pattern '{path_pattern}' and key '{has_key}'"

    def _run_command(self, cmd: Any) -> Dict[str, Any]:
        """Run a command and return stdout/stderr plus pass/fail metadata."""
        try:
            if isinstance(cmd, str):
                # String command: use ddev exec to run inside the DDEV environment.
                # ddev exec takes the full command as arguments (e.g., ["ddev", "exec", "drush", "pm:list"])
                run_cmd = ["ddev", "exec", "--", cmd]
            else:
                # List command: run directly (caller already constructed the full command list).
                run_cmd = cmd
            result = subprocess.run(
                run_cmd,
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=self._timeout_value(self.command_timeout_seconds),
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
                timeout=self._timeout_value(self.command_timeout_seconds),
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
        """Compute the final hybrid score (Task 4.2-4.4)."""
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

        # 2. Check for critical failures (Task 4.2)
        critical_fail = any(r.is_critical and not r.passed for r in check_results)

        # 3. Hybrid composition (Task 4.3)
        if judge_score is not None and judge_weight > 0:
            final_score = (deterministic_score * deterministic_weight) + (judge_score * judge_weight)
        else:
            # Task 4.4 Deterministic-only fallback
            final_score = deterministic_score

        # If any critical check failed, passed is False (Task 4.2)
        passed = not critical_fail and final_score >= scoring_config.get("threshold", 0.7)

        return HybridScore(
            deterministic_score=deterministic_score,
            judge_score=judge_score,
            final_score=final_score,
            check_results=check_results,
            passed=passed,
        )
