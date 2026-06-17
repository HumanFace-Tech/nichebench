"""Static analysis config patching for runtime workspace.

Ownership
--------
This module is owned by the workspace package.  It contains
``patch_static_analysis_configs`` which is called from ``Workspace`` before
the agent runs.

Side-effect boundaries
---------------------
- Reads and writes phpstan.neon and composer.json in the workspace.
- Commits patches to a private branch so they are excluded from final.diff.
- Logs all operations to the provided ``command_log`` list.
- Does NOT own workspace creation or DDEV operations.
"""

import re
import subprocess
from pathlib import Path
from typing import Optional


def patch_static_analysis_configs(
    path: Path,
    command_log: list[dict[str, object]],
    timeout: Optional[int] = None,
) -> Optional[str]:
    """Patch phpstan.neon and composer.json before the agent runs.

    phpstan.neon: removes the ``includes:`` block whose entries are already
    auto-loaded by phpstan/extension-installer, preventing the
    "files included multiple times" fatal error.

    composer.json: removes the hardcoded ``web/modules/custom`` path from
    the ``cs`` (phpcs) script so the path argument supplied by check specs
    is the only target, avoiding false failures from pre-existing violations
    in other modules.

    Both patches are committed so they don't appear in final.diff (which
    is diffed against the returned SHA, not the original resolved_sha).

    Args:
        path: Workspace path.
        command_log: Shared command log list to append entries to.
        timeout: Optional command timeout in seconds.

    Returns:
        The new HEAD SHA after committing, or None if no patches were
        applied or the commit failed.
    """
    patches: dict[str, str] = {}

    # 1. phpstan.neon — remove manual includes (extension-installer handles them),
    #    remove deprecated drupal.drupal_root parameter, and narrow paths to
    #    only the agent module so pre-existing nichejobs_core violations don't
    #    contaminate the check.
    phpstan_neon = path / "phpstan.neon"
    if phpstan_neon.exists():
        content = phpstan_neon.read_text()
        # Remove the includes: block
        new_content = re.sub(
            r"^includes:\n(?:  - [^\n]+\n)+\n?",
            "",
            content,
            flags=re.MULTILINE,
        )
        # Remove deprecated drupal: subsection (drupal_root is auto-discovered)
        new_content = re.sub(
            r"\n  drupal:\n(?:    [^\n]+\n)+",
            "\n",
            new_content,
        )
        # Narrow paths: from all of web/modules/custom to just the agent module
        new_content = re.sub(
            r"(    - )web/modules/custom\n",
            r"\1web/modules/custom/nichejobs_application\n",
            new_content,
        )
        if new_content != content:
            patches["phpstan.neon"] = new_content

    # 2. composer.json — drop hardcoded scan path from 'cs' and 'cs-fix' scripts
    #    so the check runner and the agent can supply a targeted path.
    composer_json_path = path / "composer.json"
    if composer_json_path.exists():
        content = composer_json_path.read_text()
        # Targeted substitution preserves original JSON formatting
        new_content = re.sub(
            r'("cs":\s*"phpcs [^"]*?) web/modules/custom(")',
            r"\1\2",
            content,
        )
        new_content = re.sub(
            r'("cs-fix":\s*"phpcbf [^"]*?) web/modules/custom(")',
            r"\1\2",
            new_content,
        )
        if new_content != content:
            patches["composer.json"] = new_content

    if not patches:
        return None

    for rel_path, new_content in patches.items():
        (path / rel_path).write_text(new_content)

    try:
        subprocess.run(
            ["git", "add"] + list(patches.keys()),
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=nichebench",
                "-c",
                "user.email=bench@local",
                "commit",
                "-m",
                "harness: fix static analysis configs for isolated check runs",
            ],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        new_sha = result.stdout.strip()
        command_log.append(
            {
                "command": "patch_static_analysis_configs",
                "returncode": 0,
                "stdout": (f"Committed patches {list(patches.keys())} → {new_sha}"),
                "stderr": "",
            }
        )
        return new_sha
    except subprocess.CalledProcessError as exc:
        command_log.append(
            {
                "command": "patch_static_analysis_configs",
                "returncode": getattr(exc, "returncode", 1),
                "stdout": getattr(exc, "stdout", ""),
                "stderr": getattr(exc, "stderr", str(exc)),
            }
        )
        return None
