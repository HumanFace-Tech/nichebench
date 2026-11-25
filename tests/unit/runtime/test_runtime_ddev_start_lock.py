"""Unit tests for the host-wide DDEV start lock.

The lock protects the shared ``ddev-router`` container: two projects that
run ``ddev start`` at the same time otherwise race to create it and the
loser fails with ``Conflict ... /ddev-router already in use``.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

import pytest

from nichebench.execution.runtime import concurrency
from nichebench.execution.runtime.workspace import ddev as ddev_module

# ---------------------------------------------------------------------------
# Lock primitive
# ---------------------------------------------------------------------------


def test_lock_file_lives_in_the_cache_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    with concurrency.exclusive_ddev_start_lock(timeout_seconds=5):
        assert (tmp_path / "ddev-start.lock").exists()


def _locked_section(
    order: list[str],
    name: str,
    holding: threading.Event,
    release: threading.Event,
) -> None:
    """Helper that runs a critical section under the DDEV start lock."""

    with concurrency.exclusive_ddev_start_lock(timeout_seconds=10):
        order.append(f"{name}-in")
        holding.set()
        release.wait(10)
        order.append(f"{name}-out")


def test_two_threads_cannot_overlap_inside_the_lock(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    order: list[str] = []
    holding = threading.Event()
    release = threading.Event()

    def first() -> None:
        _locked_section(order, "first", holding, release)

    def second() -> None:
        holding.wait(10)
        _locked_section(order, "second", holding, release)

    first_thread = threading.Thread(target=first, daemon=True)
    first_thread.start()
    assert holding.wait(5), "the first caller never took the lock"

    second_thread = threading.Thread(target=second, daemon=True)
    second_thread.start()
    time.sleep(0.3)

    # The second caller is still queued on the lock.
    assert order == ["first-in"]

    release.set()
    first_thread.join(5)
    second_thread.join(5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert order == ["first-in", "first-out", "second-in", "second-out"]


def test_lock_timeout_raises_a_clear_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    holding = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with concurrency.exclusive_ddev_start_lock(timeout_seconds=10):
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert holding.wait(5), "the lock was never taken"

    with (
        pytest.raises(concurrency.RuntimeHostLockTimeoutError) as excinfo,
        concurrency.exclusive_ddev_start_lock(timeout_seconds=0.25),
    ):
        pass

    message = str(excinfo.value)
    assert "DDEV start lock" in message
    assert "0.25" in message

    release.set()
    thread.join(5)


_CHILD_SCRIPT = """
import sys
from pathlib import Path
from nichebench.execution.runtime import concurrency

try:
    with concurrency.exclusive_ddev_start_lock(timeout_seconds=1):
        print("acquired")
except concurrency.RuntimeHostLockTimeoutError:
    print("blocked")
"""


def _run_child_process(lock_dir: Path) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(lock_dir)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return completed.stdout.strip()


def test_lock_works_across_processes(tmp_path: Path, monkeypatch):
    """A separate process cannot take the lock that this process holds."""
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    with concurrency.exclusive_ddev_start_lock(timeout_seconds=5):
        assert _run_child_process(tmp_path) == "blocked"

    assert _run_child_process(tmp_path) == "acquired"


# ---------------------------------------------------------------------------
# ddev_start integration
# ---------------------------------------------------------------------------


class _LockTracker:
    """Track lock acquisitions and detect nested acquisition."""

    def __init__(self) -> None:
        self.acquisitions = 0
        self.max_depth = 0
        self._depth = 0

    @contextlib.contextmanager
    def lock(self, *args: object, **kwargs: object) -> Iterator[None]:
        self.acquisitions += 1
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        if self._depth > 1:
            raise AssertionError("the DDEV start lock was acquired more than once at a time")
        try:
            yield
        finally:
            self._depth -= 1

    @property
    def held(self) -> bool:
        return self._depth > 0


def _cp(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, "ok", "")


def _run_ddev_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    start_side_effect: Callable[[list[str]], None] | None = None,
) -> tuple[_LockTracker, list[list[str]], list[bool]]:
    """Run ``ddev_start`` with mocked commands and a tracked lock.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: Temporary directory used as the workspace path.
        start_side_effect: Optional callable invoked for each ``ddev start``.

    Returns:
        The lock tracker, the list of executed commands, and for each command
        whether the lock was held while it ran.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    tracker = _LockTracker()
    executed: list[list[str]] = []
    lock_states: list[bool] = []

    def fake_lock(*args: object, **kwargs: object):
        return tracker.lock(*args, **kwargs)

    def run_logged(command, path=None, command_log=None, timeout=None):
        executed.append(list(command))
        lock_states.append(tracker.held)
        if command[:2] == ["ddev", "start"] and start_side_effect is not None:
            start_side_effect(command)
        return _cp(list(command))

    monkeypatch.setattr(ddev_module, "exclusive_ddev_start_lock", fake_lock)
    monkeypatch.setattr(ddev_module, "run_logged_command", run_logged)
    monkeypatch.setattr(
        "nichebench.execution.runtime.workspace.cleanup.best_effort_network_hygiene",
        lambda *args, **kwargs: None,
    )

    ddev_module.ddev_start(
        path=workspace,
        setup_mode="db_snapshot",
        ddev_project_name="nb-drupal-runtime-001",
        command_log=[],
        setup_warnings=[],
    )
    return tracker, executed, lock_states


def test_ddev_start_takes_the_lock_exactly_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    tracker, executed, lock_states = _run_ddev_start(monkeypatch, tmp_path)

    assert tracker.acquisitions == 1
    assert tracker.max_depth == 1

    start_index = executed.index(["ddev", "start"])
    assert lock_states[start_index] is True, "ddev start must run under the lock"

    # The lock is released before the slower setup steps.
    composer_index = executed.index(["ddev", "composer", "install"])
    assert lock_states[composer_index] is False, "the lock must not be held during composer install"


def test_ddev_start_takes_the_lock_exactly_once_on_the_retry_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
):
    calls = {"start": 0}

    def start_side_effect(command: list[str]) -> None:
        calls["start"] += 1
        if calls["start"] == 1:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                stderr="all predefined address pools have been fully subnetted",
            )

    tracker, executed, lock_states = _run_ddev_start(monkeypatch, tmp_path, start_side_effect)

    assert calls["start"] == 2, "the start must be retried once"
    assert tracker.acquisitions == 1, "the retry must reuse the same lock"
    assert tracker.max_depth == 1, "the lock must never be nested"

    for index, command in enumerate(executed):
        if command[:2] == ["ddev", "start"]:
            assert lock_states[index] is True, "both start attempts must run under the lock"


def test_ddev_start_lock_is_not_held_during_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The lock covers the start only — never the setup that follows it."""
    tracker, executed, lock_states = _run_ddev_start(monkeypatch, tmp_path)

    assert tracker.acquisitions == 1
    after_start = executed.index(["ddev", "start"]) + 1
    assert all(state is False for state in lock_states[after_start:])


def test_concurrent_ddev_start_calls_do_not_overlap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Two parallel projects must not run ``ddev start`` at the same time.

    This is the end-to-end check for the ``ddev-router`` conflict, using the
    real lock: the second project's start waits until the first one released
    it, so the shared container is created exactly once.
    """
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    intervals: list[tuple[float, float]] = []
    guard = threading.Lock()

    def run_logged(command, path=None, command_log=None, timeout=None):
        if command[:2] == ["ddev", "start"]:
            begin = time.monotonic()
            time.sleep(0.3)
            end = time.monotonic()
            with guard:
                intervals.append((begin, end))
        return _cp(list(command))

    monkeypatch.setattr(ddev_module, "run_logged_command", run_logged)
    monkeypatch.setattr(
        "nichebench.execution.runtime.workspace.cleanup.best_effort_network_hygiene",
        lambda *args, **kwargs: None,
    )

    def start(index: int) -> None:
        workspace = tmp_path / f"workspace-{index}"
        workspace.mkdir(parents=True, exist_ok=True)
        ddev_module.ddev_start(
            path=workspace,
            setup_mode="db_snapshot",
            ddev_project_name=f"nb-drupal-runtime-00{index}",
            command_log=[],
            setup_warnings=[],
        )

    threads = [threading.Thread(target=start, args=(index,), daemon=True) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert all(not thread.is_alive() for thread in threads)
    assert len(intervals) == 2
    first, second = intervals
    assert first[1] <= second[0], "the two ddev start calls overlapped"
