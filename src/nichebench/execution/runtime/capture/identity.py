"""Capture of the *effective* OpenCode configuration and binary identity.

The generated ``opencode.json`` is what actually governs the agent, so the
capture stores it — with every credential-looking field replaced before it hits
disk, and with a manifest recording what was removed. The real key never enters
the archive.

Binary identity is a cheap offline ``docker image inspect``: image id plus the
pinned OpenCode version string. Both are recorded so a trace can be tied to the
exact agent build that produced it.

Ownership: identity + config capture only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from nichebench.execution.runtime.capture.run import write_private_json
from nichebench.execution.runtime.capture.scrub import is_credential_value, scrub_known_credentials

_CONFIG_MANIFEST_NAME = "opencode_config_manifest.json"

# Identity probes are cheap but must never become a latency risk on the critical
# path: a slow Docker daemon costs a couple of seconds, not the whole run.
IDENTITY_TIMEOUT_SECONDS = 3.0


def capture_opencode_config(
    config_path: Path,
    attempt_dir: Path,
    known_credentials: Iterable[str] = (),
) -> Dict[str, Any]:
    """Copy the generated cage ``opencode.json`` into the capture, sanitised.

    The provider key is injected via env, not stored in the config, but the
    config can still declare ``apiKey``/``authorization`` in provider options.
    Those are replaced structurally; the removal list is recorded so the file
    is never presented as an exact byte copy when something was removed.

    Args:
        config_path: Path to the generated ``opencode.json``.
        attempt_dir: Attempt directory that receives the sanitised copy.

    Returns:
        Manifest dict describing what was stored and what was removed.
    """
    manifest: Dict[str, Any] = {"source": str(config_path), "stored": False}
    if not config_path.exists():
        manifest["error"] = "config_missing"
        return manifest
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        manifest["error"] = f"config_unreadable: {type(exc).__name__}"
        return manifest

    removed: list[str] = []

    def _collect(node: Any, prefix: str) -> Any:
        if isinstance(node, dict):
            out: Dict[str, Any] = {}
            for key, value in node.items():
                if is_credential_value(str(key), value):
                    removed.append(f"{prefix}.{key}" if prefix else str(key))
                    out[str(key)] = "[REDACTED]"
                else:
                    out[str(key)] = _collect(value, f"{prefix}.{key}" if prefix else str(key))
            return out
        if isinstance(node, list):
            return [_collect(item, f"{prefix}[]") for item in node]
        if isinstance(node, str):
            clean, hits = scrub_known_credentials(node, known_credentials)
            if hits:
                removed.append(prefix)
            return clean
        return node

    sanitized = _collect(raw, "")
    stored_path = attempt_dir / "opencode.json"
    write_private_json(stored_path, sanitized)
    manifest["stored"] = True
    manifest["stored_path"] = str(stored_path)
    manifest["exact_bytes"] = False
    manifest["exclusions"] = ["json_reserialized", *(["credentials_removed"] if removed else [])]
    manifest["removed_credential_fields"] = removed
    write_private_json(attempt_dir / _CONFIG_MANIFEST_NAME, manifest)
    return manifest


def inspect_image_identity(
    image: str,
    docker_binary: str = "docker",
    timeout_seconds: float = IDENTITY_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Return cheap offline identity for the cage image.

    Args:
        image: Image reference used for the run.
        docker_binary: Docker CLI executable.
        timeout_seconds: Hard cap for the inspect call.

    Returns:
        Dict with ``image``, ``image_id``, and ``inspected`` (bool). Missing
        Docker yields ``inspected: False`` rather than raising — identity is
        informative, never required.
    """
    identity: Dict[str, Any] = {"image": image, "image_id": None, "inspected": False}
    if shutil.which(docker_binary) is None:
        return identity
    try:
        completed = subprocess.run(  # noqa: S603
            [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return identity
    if completed.returncode == 0:
        image_id = (completed.stdout or "").strip()
        if image_id:
            identity["image_id"] = image_id
            identity["inspected"] = True
    return identity


def probe_opencode_version(
    image: str,
    docker_binary: str = "docker",
    timeout_seconds: float = IDENTITY_TIMEOUT_SECONDS,
) -> Optional[str]:
    """Return the OpenCode version reported by the image, or ``None``.

    Runs ``opencode --version`` inside the image. This is offline: it never
    contacts the provider.
    """
    if shutil.which(docker_binary) is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603
            [
                docker_binary,
                "run",
                "--rm",
                "-e",
                "HOME=/tmp",
                "--entrypoint",
                "opencode",
                image,
                "--version",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    version = (completed.stdout or "").strip()
    return version or None
