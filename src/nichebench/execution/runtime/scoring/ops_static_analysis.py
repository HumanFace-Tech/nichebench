"""Runtime scoring: static analysis check operations.

Owner: scoring package — ops layer.
Boundary: all check operations that run static analysis tools (PHPStan, PHPCS)
via ``ddev composer`` or ``ddev exec``.  These operations always go through
DDEV; they do not assume the host has phpstan or composer installed.

Supported check types
---------------------
composer_script_clean — run a composer script (e.g. ``cs`` for PHPCS) and
                        expect return code 0.
phpstan_clean         — run PHPStan via ``ddev composer stan`` or
                        ``ddev exec vendor/bin/phpstan`` with optional paths.
"""

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple


def op_composer_script_clean(
    workspace_path: Path,
    command_timeout_seconds: int,
    spec: Dict[str, Any],
) -> Tuple[bool, str, Dict[str, Any]]:
    """Run a composer script and check it passes (return code 0)."""
    import shutil

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
            timeout=max(command_timeout_seconds, 1),
            cwd=workspace_path,
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
            f"Composer script '{script}' timed out after {command_timeout_seconds}s",
            {
                "command": " ".join(cmd),
                "stdout": "",
                "stderr": "timeout",
                "returncode": None,
            },
        )


def op_phpstan_clean(
    workspace_path: Path,
    command_timeout_seconds: int,
    spec: Dict[str, Any],
) -> Tuple[bool, str, Dict[str, Any]]:
    """Run PHPStan and check it passes (return code 0)."""
    import shutil

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

    raw_args = [str(arg) for arg in spec.get("args", [])]
    paths: List[str] = []
    unsupported_args: List[str] = []
    for arg in raw_args:
        if arg.startswith("--paths="):
            path_value = arg.removeprefix("--paths=").strip()
            if path_value:
                paths.append(path_value)
            continue
        if arg and not arg.startswith("-"):
            paths.append(arg)
            continue
        unsupported_args.append(arg)
    if unsupported_args:
        return (
            False,
            f"Unsupported phpstan_clean args: {', '.join(unsupported_args)}",
            {
                "command": "ddev exec -- vendor/bin/phpstan analyse --configuration=phpstan.neon",
                "stdout": "",
                "stderr": f"Unsupported args: {unsupported_args}",
                "returncode": 2,
            },
        )
    if paths:
        cmd = ["ddev", "exec", "--", "vendor/bin/phpstan", "analyse", "--configuration=phpstan.neon"] + paths
    else:
        cmd = ["ddev", "composer", "stan"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(command_timeout_seconds, 1),
            cwd=workspace_path,
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
            f"PHPStan timed out after {command_timeout_seconds}s",
            {
                "command": " ".join(cmd),
                "stdout": "",
                "stderr": "timeout",
                "returncode": None,
            },
        )
