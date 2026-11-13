"""Unit tests for the host-level runtime concurrency primitives."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from nichebench.execution.runtime import concurrency


def _hold_slot(root: Path, slots: int, holding: threading.Event, release: threading.Event) -> threading.Thread:
    """Start a thread that holds a host slot until ``release`` is set."""

    def _holder() -> None:
        with concurrency.runtime_host_slots(slots, timeout_seconds=10, root=root):
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=_holder, daemon=True)
    thread.start()
    assert holding.wait(5), "the slot was never taken"
    return thread


def test_slot_file_is_created_and_released(tmp_path: Path):
    with concurrency.runtime_host_slots(1, timeout_seconds=1, root=tmp_path) as slots:
        assert slots == 1
        assert (tmp_path / "slot-0.lock").exists()

    # The lock is released on exit, so the next acquirer gets it immediately.
    with concurrency.runtime_host_slots(1, timeout_seconds=1, root=tmp_path):
        pass


def test_second_acquirer_blocks_until_the_first_releases(tmp_path: Path):
    holding = threading.Event()
    release = threading.Event()
    thread = _hold_slot(tmp_path, 1, holding, release)

    with (
        pytest.raises(concurrency.RuntimeSlotTimeoutError),
        concurrency.runtime_host_slots(1, timeout_seconds=0.5, root=tmp_path),
    ):
        pass

    release.set()
    thread.join(5)
    assert not thread.is_alive()

    # The slot is free again, so the next acquirer succeeds.
    with concurrency.runtime_host_slots(1, timeout_seconds=1, root=tmp_path):
        pass


def test_timeout_message_names_the_slots_and_the_timeout(tmp_path: Path):
    holding = threading.Event()
    release = threading.Event()
    thread = _hold_slot(tmp_path, 1, holding, release)

    with (
        pytest.raises(concurrency.RuntimeSlotTimeoutError) as excinfo,
        concurrency.runtime_host_slots(1, timeout_seconds=0.25, root=tmp_path),
    ):
        pass

    message = str(excinfo.value)
    assert "runtime_max_concurrent_projects=1" in message
    assert "0.25" in message

    release.set()
    thread.join(5)


def test_max_slots_is_clamped_to_at_least_one(tmp_path: Path):
    with concurrency.runtime_host_slots(0, timeout_seconds=1, root=tmp_path) as slots:
        assert slots == 1
        assert (tmp_path / "slot-0.lock").exists()
    assert not (tmp_path / "slot-1.lock").exists()


def test_zero_slots_behaves_like_a_single_slot(tmp_path: Path):
    holding = threading.Event()
    release = threading.Event()
    thread = _hold_slot(tmp_path, 0, holding, release)

    with (
        pytest.raises(concurrency.RuntimeSlotTimeoutError),
        concurrency.runtime_host_slots(0, timeout_seconds=0.5, root=tmp_path),
    ):
        pass

    release.set()
    thread.join(5)


def test_two_slots_allow_two_holders(tmp_path: Path):
    holding = threading.Event()
    release = threading.Event()
    thread = _hold_slot(tmp_path, 2, holding, release)

    # A second holder fits because two slots exist.
    with concurrency.runtime_host_slots(2, timeout_seconds=1, root=tmp_path):
        pass

    release.set()
    thread.join(5)


def test_env_var_overrides_the_slot_directory(tmp_path: Path, monkeypatch):
    slot_dir = tmp_path / "custom-slots"
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(slot_dir))

    with concurrency.runtime_host_slots(1, timeout_seconds=1):
        assert (slot_dir / "slot-0.lock").exists()


def test_without_fcntl_the_slot_is_a_no_op(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(concurrency, "fcntl", None)

    with concurrency.runtime_host_slots(1, timeout_seconds=0.1, root=tmp_path) as slots:
        assert slots == 1

    assert not (tmp_path / "slot-0.lock").exists()


_CHILD_SCRIPT = """
import sys
from pathlib import Path
from nichebench.execution.runtime import concurrency

try:
    with concurrency.runtime_host_slots(1, timeout_seconds=1, root=Path(sys.argv[1])):
        print("acquired")
except concurrency.RuntimeSlotTimeoutError:
    print("blocked")
"""


def _run_child_process(slot_dir: Path) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(slot_dir)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return completed.stdout.strip()


def test_slot_lock_works_across_processes(tmp_path: Path):
    """A separate CLI process cannot take a slot that this process holds."""
    with concurrency.runtime_host_slots(1, timeout_seconds=1, root=tmp_path):
        assert _run_child_process(tmp_path) == "blocked"

    # Once the slot is released the other process can take it.
    assert _run_child_process(tmp_path) == "acquired"


def test_default_slot_dir_uses_xdg_cache_home(monkeypatch, tmp_path):
    from nichebench.execution.runtime import concurrency

    monkeypatch.delenv(concurrency.SLOT_DIR_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert concurrency._cache_dir() == tmp_path / concurrency.DEFAULT_SLOT_DIR
