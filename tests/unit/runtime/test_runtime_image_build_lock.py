"""Unit tests for the shared DDEV image build lock."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from nichebench.execution.runtime import concurrency, image


def test_exclusive_build_lock_serializes_two_threads(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    order: list[str] = []
    holding = threading.Event()
    release = threading.Event()

    def first() -> None:
        with concurrency.exclusive_build_lock("nichebench/opencode-ddev:2.2.5"):
            order.append("first-in")
            holding.set()
            release.wait(10)
            order.append("first-out")

    def second() -> None:
        holding.wait(10)
        with concurrency.exclusive_build_lock("nichebench/opencode-ddev:2.2.5"):
            order.append("second-in")

    first_thread = threading.Thread(target=first, daemon=True)
    first_thread.start()
    assert holding.wait(5)

    second_thread = threading.Thread(target=second, daemon=True)
    second_thread.start()
    time.sleep(0.3)

    # The second caller is still waiting for the lock.
    assert order == ["first-in"]

    release.set()
    first_thread.join(5)
    second_thread.join(5)
    assert order == ["first-in", "first-out", "second-in"]


def test_exclusive_build_lock_uses_a_sanitised_tag_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    with concurrency.exclusive_build_lock("nichebench/opencode-ddev:2.2.5"):
        pass

    lock_files = list(tmp_path.glob("build-*.lock"))
    assert len(lock_files) == 1
    assert "nichebench-opencode-ddev-2.2.5" in lock_files[0].name


def test_second_caller_reuses_the_newly_built_image(tmp_path: Path, monkeypatch):
    """A caller that waited for the build lock must not build the tag twice."""
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    state = {"built": False}
    build_calls: list[str] = []

    def probe(candidate: str) -> bool:
        if candidate == "base:latest":
            return False
        return state["built"]

    def build(base_image: str, ddev_image: str) -> None:
        build_calls.append(ddev_image)
        # Give the second caller time to queue up on the build lock.
        time.sleep(0.2)
        state["built"] = True

    runtime_config = {
        "runtime_container_enable_ddev": True,
        "runtime_container_image": "base:latest",
        "runtime_container_ddev_image": "derived:1",
        "runtime_container_ddev_auto_build": True,
    }

    resolved: list[str] = []

    def resolve() -> None:
        resolved.append(image.resolve_effective_cage_image(runtime_config, probe, build, RuntimeError))

    threads = [threading.Thread(target=resolve, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert resolved == ["derived:1", "derived:1"]
    assert build_calls == ["derived:1"]


def test_build_still_happens_when_the_image_is_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    build_calls: list[str] = []

    def probe(candidate: str) -> bool:
        return candidate == "derived:1" and bool(build_calls)

    def build(base_image: str, ddev_image: str) -> None:
        build_calls.append(ddev_image)

    runtime_config = {
        "runtime_container_enable_ddev": True,
        "runtime_container_image": "base:latest",
        "runtime_container_ddev_image": "derived:1",
        "runtime_container_ddev_auto_build": True,
    }

    resolved = image.resolve_effective_cage_image(runtime_config, probe, build, RuntimeError)

    assert resolved == "derived:1"
    assert build_calls == ["derived:1"]


def test_auto_build_disabled_still_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(concurrency.SLOT_DIR_ENV_VAR, str(tmp_path))

    runtime_config = {
        "runtime_container_enable_ddev": True,
        "runtime_container_image": "base:latest",
        "runtime_container_ddev_image": "derived:1",
        "runtime_container_ddev_auto_build": False,
    }

    with pytest.raises(RuntimeError, match="auto_build is disabled"):
        image.resolve_effective_cage_image(runtime_config, lambda _candidate: False, lambda *_: None, RuntimeError)
