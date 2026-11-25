"""Host-level concurrency primitives for runtime task execution.

Module scope
───────────
This module owns the host-wide coordination primitives used by the runtime
executor:

- ``runtime_host_slots`` — a semaphore built from ``flock``-ed slot files that
  caps how many runtime projects may run concurrently on one host.
- ``exclusive_build_lock`` — a per-tag lock that serialises Docker image
  builds of the same derived image across processes.
- ``exclusive_ddev_start_lock`` — a host-wide lock that serialises
  ``ddev start`` calls, so parallel projects do not race to create the
  shared ``ddev-router`` container.

This module does NOT own:
- DDEV project lifecycle or workspace cleanup
- Cage container execution
- Scoring, judging, or result reporting

Operational constraints
───────────────────────
- Slot files live in ``~/.cache/nichebench/runtime-slots`` by default. The
  directory can be overridden with the ``NICHEBENCH_RUNTIME_SLOT_DIR``
  environment variable, which keeps test runs and parallel runs isolated.
- ``fcntl.flock`` locks are attached to the open file description, so two
  *separate* opens conflict even inside one process. Every acquirer therefore
  opens its own descriptor; nested acquisition of the same slot deadlocks by
  design.
- When ``fcntl`` is unavailable (non-POSIX platforms) the primitives degrade
  to a no-op that still yields, so the harness keeps working — the host simply
  stops enforcing the cap.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Iterator, List, Optional

try:  # pragma: no cover - platform dependent
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

SLOT_DIR_ENV_VAR = "NICHEBENCH_RUNTIME_SLOT_DIR"
DEFAULT_SLOT_DIR = Path("nichebench") / "runtime-slots"
DEFAULT_CACHE_HOME = Path(".cache")
POLL_INTERVAL_SECONDS = 0.25

# Lock file used by ``exclusive_ddev_start_lock``.  The name is deliberately
# not project-scoped: the conflict it protects against (two projects racing
# to create the shared ``ddev-router`` container) is host-wide.
DDEV_START_LOCK_FILENAME = "ddev-start.lock"
DEFAULT_DDEV_START_LOCK_TIMEOUT_SECONDS = 600.0

# Characters kept verbatim in a sanitised tag; everything else becomes "-".
_SAFE_TAG_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class RuntimeHostLockTimeoutError(RuntimeError):
    """Raised when a host-level runtime lock cannot be acquired in time."""


class RuntimeSlotTimeoutError(RuntimeHostLockTimeoutError):
    """Raised when a runtime host slot cannot be acquired in time."""


def _cache_dir(root: Optional[Path] = None) -> Path:
    """Resolve the directory that holds slot and build-lock files.

    Args:
        root: Explicit directory override. When ``None`` the
            ``NICHEBENCH_RUNTIME_SLOT_DIR`` environment variable is used, and
            finally ``~/.cache/nichebench/runtime-slots``.

    Returns:
        The resolved directory (not created by this function).
    """
    if root is not None:
        return Path(root)
    env_value = os.environ.get(SLOT_DIR_ENV_VAR)
    if env_value:
        return Path(env_value)
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / DEFAULT_CACHE_HOME))
    return cache_home / DEFAULT_SLOT_DIR


def _sanitize_tag(tag: str) -> str:
    """Turn an image tag into a safe, collision-resistant file name stem.

    Args:
        tag: Raw image tag (e.g. ``"nichebench/opencode-ddev:2.2.5"``).

    Returns:
        A file-name-safe stem. The digest suffix keeps two different tags
        that sanitise to the same text apart.
    """
    text = str(tag)
    safe = _SAFE_TAG_CHARS.sub("-", text).strip("-") or "tag"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{safe[:64]}-{digest}"


def _try_lock(path: Path) -> Optional[int]:
    """Try to take an exclusive, non-blocking lock on ``path``.

    Args:
        path: Lock file to open (created when missing).

    Returns:
        The open file descriptor when the lock was taken, ``None`` when the
        lock is held by someone else.
    """
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[union-attr]
    except OSError:
        os.close(fd)
        return None
    return fd


def _release(fd: int) -> None:
    """Release a lock and close its descriptor, ignoring teardown errors."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _acquire_slot(slot_dir: Path, index: int) -> Optional[int]:
    """Try to lock the slot file with the given index."""
    return _try_lock(slot_dir / f"slot-{index}.lock")


@contextlib.contextmanager
def runtime_host_slots(
    max_slots: int,
    timeout_seconds: float = 3600,
    root: Optional[Path] = None,
) -> Iterator[int]:
    """Hold one of ``max_slots`` host-wide runtime slots for the block body.

    The context manager is a cross-process semaphore: it takes an exclusive
    ``flock`` on one of ``max_slots`` slot files and holds it until the block
    exits. While the slot is held no other thread or process may take it, so
    at most ``max_slots`` runtime projects run concurrently on one host.

    Args:
        max_slots: Number of slots available on the host. Values below ``1``
            are clamped to ``1``.
        timeout_seconds: How long to wait for a free slot before giving up.
        root: Directory holding the slot files. Defaults to the
            ``NICHEBENCH_RUNTIME_SLOT_DIR`` environment variable and finally
            ``~/.cache/nichebench/runtime-slots``.

    Yields:
        The effective number of slots.

    Raises:
        RuntimeSlotTimeoutError: When no slot became free within
            ``timeout_seconds``.
    """
    try:
        slots = int(max_slots)
    except (TypeError, ValueError):
        slots = 1
    slots = max(1, slots)
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 3600.0

    if fcntl is None:
        # No advisory locking on this platform: run unguarded rather than
        # refusing to execute at all.
        yield slots
        return

    slot_dir = _cache_dir(root)
    try:
        slot_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield slots
        return

    deadline = time.monotonic() + max(0.0, timeout)
    held: List[int] = []
    try:
        while True:
            for index in range(slots):
                try:
                    fd = _acquire_slot(slot_dir, index)
                except OSError:
                    # The slot directory is unusable on this host: run
                    # unguarded rather than failing the task.
                    yield slots
                    return
                if fd is not None:
                    held.append(fd)
                    break
            if held:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeSlotTimeoutError(
                    f"Could not acquire a runtime host slot within {timeout:g} seconds "
                    f"(runtime_max_concurrent_projects={slots}, slot dir {slot_dir})"
                )
            time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
        yield slots
    finally:
        for fd in held:
            _release(fd)


@contextlib.contextmanager
def exclusive_build_lock(tag: str, timeout_seconds: float = 1800) -> Iterator[None]:
    """Serialise image builds for the same tag across processes.

    The lock is keyed on the image tag, so two processes that want to build
    the same derived image queue up instead of building it twice.

    Args:
        tag: Image tag whose build must be exclusive.
        timeout_seconds: How long to wait for the lock before giving up.

    Yields:
        ``None`` once the lock is held.

    Raises:
        RuntimeHostLockTimeoutError: When the lock was not acquired within
            ``timeout_seconds``.
    """
    if fcntl is None:
        yield
        return

    lock_dir = _cache_dir()
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield
        return

    lock_path = lock_dir / f"build-{_sanitize_tag(tag)}.lock"
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 1800.0

    deadline = time.monotonic() + max(0.0, timeout)
    fd: Optional[int] = None
    try:
        while True:
            try:
                fd = _try_lock(lock_path)
            except OSError:
                # The lock file cannot be created on this host: build without
                # the lock rather than failing the task.
                yield
                return
            if fd is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeHostLockTimeoutError(
                    f"Timed out after {timeout:g} seconds waiting for the image build lock "
                    f"of '{tag}'"
                )
            time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
        yield
    finally:
        if fd is not None:
            _release(fd)


@contextlib.contextmanager
def exclusive_ddev_start_lock(timeout_seconds: float = 600) -> Iterator[None]:
    """Serialise ``ddev start`` calls across processes on one host.

    ``ddev start`` creates a shared ``ddev-router`` container the first time
    it runs on a host.  Two projects starting at the same time both try to
    create it and the loser fails with
    ``Conflict. The container name "/ddev-router" is already in use``.
    Holding this lock around the start attempt (and its retry) makes the
    creation happen exactly once.

    The lock is host-wide, not project-scoped, because the resource it
    protects is host-wide.  It uses the same ``flock`` primitive as
    :func:`exclusive_build_lock`, so it serialises separate threads *and*
    separate processes.  Because ``flock`` locks are attached to the open
    file description, nested acquisition of this lock deadlocks by design —
    callers must acquire it exactly once per start attempt.

    Args:
        timeout_seconds: How long to wait for the lock before giving up.

    Yields:
        ``None`` once the lock is held.

    Raises:
        RuntimeHostLockTimeoutError: When the lock was not acquired within
            ``timeout_seconds``.
    """
    if fcntl is None:
        yield
        return

    lock_dir = _cache_dir()
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield
        return

    lock_path = lock_dir / DDEV_START_LOCK_FILENAME
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = DEFAULT_DDEV_START_LOCK_TIMEOUT_SECONDS

    deadline = time.monotonic() + max(0.0, timeout)
    fd: Optional[int] = None
    try:
        while True:
            try:
                fd = _try_lock(lock_path)
            except OSError:
                # The lock file cannot be created on this host: start without
                # the lock rather than failing the task.
                yield
                return
            if fd is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeHostLockTimeoutError(
                    f"Timed out after {timeout:g} seconds waiting for the DDEV start lock "
                    f"({lock_path})"
                )
            time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
        yield
    finally:
        if fd is not None:
            _release(fd)
