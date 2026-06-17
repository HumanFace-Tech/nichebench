"""Runtime cage image resolution and DDEV image support.

Module scope
───────────
This module owns the runtime cage *image resolution* step only. It determines
which Docker image the cage container will use and whether that image has the
DDEV toolchain available.

This module does NOT own:
- Cage execution or container lifecycle (``opencode run`` wrapper)
- Workspace creation or cleanup
- Drush command execution or Drupal environment setup
- Checks pipeline or scoring logic

Callers are expected to use the single public entry point
``resolve_effective_cage_image()`` and pass injectable ``probe`` and ``build``
callables so the module remains testable and decoupled from subprocess details.

Operational constraints
──────────────────────
- ``probe_image_for_ddev`` requires the image to expose ``ddev``, ``docker``,
  and ``git`` on the default ``PATH`` inside the container. If any binary is
  missing the probe returns ``False``.
- ``build_ddev_image`` produces a derived image tagged ``ddev_image`` using a
  Dockerfile located at ``dockerfile_path``. The build is synchronous and blocks
  for up to 300 s before raising.
- ``resolve_effective_cage_image`` follows a strict fallback chain:

  1. DDEV disabled → return ``base_image`` unchanged.
  2. ``probe(base_image)`` succeeds → return ``base_image`` (already DDEV-capable).
  3. ``auto_build`` enabled → run ``build(base_image, ddev_image)``, then probe
     the derived image. Return it on success; raise on failure.
  4. ``auto_build`` disabled → raise immediately, requiring callers to either
     enable auto-build or provision a compatible base image externally.

Auto-build fallback contract
────────────────────────────
When ``runtime_container_ddev_auto_build`` is ``True`` the module will attempt
to construct a DDEV-capable derived image automatically. Callers MUST ensure:
- A compatible ``dockerfile_path`` is present on disk.
- Docker build credentials / network access are available in the execution
  environment.
- The derived image tag is unique per run to avoid caching stale layers; the
  harness uses a UUID-suffixed DDEV project name to guarantee uniqueness.

If auto-build is disabled the caller is responsible for pre-provisioning a
DDEV-capable base image. The module will NOT silently fall back in this case.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def probe_image_for_ddev(image: str, subprocess_module: Any) -> bool:
    """Probe image for ddev/docker/git availability.

    Runs a ephemeral container from ``image`` and checks whether all three
    binaries required for Drupal runtime tasks are present on ``PATH``:
    ``ddev``, ``docker``, and ``git``.

    Parameters
    ----------
    image:
        The Docker image reference to probe (e.g. ``"opencode:latest"``).
    subprocess_module:
        An injectable ``subprocess`` module (or compatible mock) used to run
        the ``docker run`` command. Enables unit testing without a Docker daemon.

    Returns
    -------
    bool
        ``True`` if all three ``command -v`` checks succeed inside the container,
        ``False`` if any check fails or the container fails to start (including
        image pull failures, non-zero exit codes, or timeout).

    Notes
    -----
    The probe uses a short-lived ``sh`` entrypoint with ``command -v`` rather
    than ``which`` for broader POSIX compatibility. The 30 s timeout prevents
    hanging on unreachable registries or misconfigured images.
    """
    try:
        result = subprocess_module.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                image,
                "-c",
                "command -v ddev && command -v docker && command -v git",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def build_ddev_image(
    base_image: str,
    ddev_image: str,
    dockerfile_path: Path,
    subprocess_module: Any,
    validation_error_cls: type[Exception],
) -> None:
    """Build DDEV-capable derived image from a Dockerfile.

    Constructs a derived image tagged ``ddev_image`` by building the Dockerfile
    at ``dockerfile_path`` with ``BASE_IMAGE`` set to ``base_image`` as a
    build-time argument. The Dockerfile is responsible for installing the DDEV
    toolchain (ddev, docker CLI, git) on top of the base image.

    Parameters
    ----------
    base_image:
        The original image reference to use as the ``FROM`` base inside the
        Dockerfile (passed as ``--build-arg BASE_IMAGE=...``).
    ddev_image:
        The target tag for the built image. Must be unique per runtime run to
        avoid layer-caching stale state; callers should include a UUID suffix.
    dockerfile_path:
        Path to the ``Dockerfile`` that installs the DDEV toolchain. The build
        context is the parent directory of this file.
    subprocess_module:
        An injectable ``subprocess`` module (or compatible mock).
    validation_error_cls:
        Exception class to raise on build failure. The harness typically passes
        a custom validation exception type so errors surface with actionable
        messages.

    Raises
    ------
    validation_error_cls
        When ``docker build`` exits with a non-zero code. The exception message
        includes the stderr output from Docker.

    Notes
    -----
    Build timeout is 300 s. This accommodates slow first-time pulls of the base
    image on cold hosts but callers should ensure network connectivity before
    invoking this function in production runs.
    """
    try:
        subprocess_module.run(
            [
                "docker",
                "build",
                "-t",
                ddev_image,
                "-f",
                str(dockerfile_path),
                "--build-arg",
                f"BASE_IMAGE={base_image}",
                str(dockerfile_path.parent),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
    except subprocess_module.CalledProcessError as e:
        raise validation_error_cls(f"Failed to build ddev-capable image: {e.stderr}")


def resolve_effective_cage_image(
    runtime_config: Dict[str, Any],
    probe: Any,
    build: Any,
    validation_error_cls: type[Exception],
) -> str:
    """Resolve effective cage image, handling DDEV capability checks and auto-build.

    This is the single public entry point for image resolution. It reads
    configuration from ``runtime_config``, delegates capability probing and
    image building to injectable callables, and returns the image reference
    that the cage container should use.

    Resolution chain
    ─────────────────
    1. ``runtime_container_enable_ddev`` is ``False`` → return
       ``runtime_container_image`` unchanged (caller handles DDEV setup).
    2. Probe ``runtime_container_image`` with ``probe()`` → returns
       ``runtime_container_image`` if it already has ddev/docker/git.
    3. ``runtime_container_ddev_auto_build`` is ``True`` → call ``build()`` to
       produce the derived DDEV image, then probe it. Return the derived image
       on success; raise ``validation_error_cls`` if the built image still fails
       the probe (indicates a broken Dockerfile or base image).
    4. ``auto_build`` is ``False`` → raise ``validation_error_cls`` immediately,
       signalling that the caller must either enable auto-build or pre-provision
       a compatible base image.

    Parameters
    ----------
    runtime_config:
        Dictionary of runtime container settings. Expected keys:

        - ``runtime_container_enable_ddev`` (``bool``) — default ``True``
        - ``runtime_container_image`` (``str``) — base image tag
        - ``runtime_container_ddev_image`` (``str``) — derived image tag;
          default ``"nichebench/opencode-ddev:1.14.25"``
        - ``runtime_container_ddev_auto_build`` (``bool``) — default ``True``

    probe:
        Callable matching the signature of ``probe_image_for_ddev(image)``.
        In production this is ``probe_image_for_ddev``; in tests a mock.
    build:
        Callable matching the signature of ``build_ddev_image(...)``.
        In production this is ``build_ddev_image``; in tests a mock.
    validation_error_cls:
        Exception class to raise on resolution failure (e.g. when auto-build
        is disabled and the base image lacks DDEV support, or when auto-build
        produces a non-functional image).

    Returns
    -------
    str
        The resolved Docker image tag to use for the cage container.

    Raises
    ------
    validation_error_cls
        When DDEV is required but unavailable and auto-build is disabled, or
        when auto-build completes but the resulting image still fails the probe.
    """
    enable_ddev = bool(runtime_config.get("runtime_container_enable_ddev", True))
    base_image = str(runtime_config.get("runtime_container_image", ""))
    ddev_image = str(runtime_config.get("runtime_container_ddev_image", "nichebench/opencode-ddev:1.14.25"))
    auto_build = bool(runtime_config.get("runtime_container_ddev_auto_build", True))

    if not enable_ddev:
        return base_image
    if probe(base_image):
        return base_image
    if auto_build:
        build(base_image, ddev_image)
        if probe(ddev_image):
            return ddev_image
        raise validation_error_cls(
            f"Derived DDEV image {ddev_image} still lacks required ddev/docker/git binaries or ddev drush support"
        )
    raise validation_error_cls(
        "Base image "
        f"{base_image} lacks required ddev/docker/git binaries or ddev drush support "
        "and auto_build is disabled"
    )
