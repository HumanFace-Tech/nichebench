"""Unit-test isolation for host-level runtime locks.

The runtime host-slot and DDEV-start locks are intentionally host-wide.  Unit
tests must never contend with a live benchmark run (or leave lock files in the
developer's home directory), so every test gets its own slot directory.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime_slot_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NICHEBENCH_RUNTIME_SLOT_DIR", str(tmp_path / "runtime-slots"))
