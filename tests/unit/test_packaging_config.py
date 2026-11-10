"""Packaging configuration guard for the repo-tree build fix (C6.2.13).

``poetry build`` walks every directory under each ``packages`` include. With a
plain ``nichebench`` include, poetry-core recursively expands ``src/nichebench``
and re-walks the 438 MB ``frameworks/*/data`` submodule once per directory,
which stalled the repo-tree build for minutes (quiz-v2 Q7.7, Plan 2 C6.2.13).

The config that keeps discovery shallow is pinned here:

* the package include is the non-recursive ``nichebench/*`` glob;
* both ``frameworks/*/data`` trees are excluded as directories — the older
  ``/**`` form made the exclude walk enumerate every subtree per entry;
* exclude patterns that exist on disk actually resolve (catches path drift).

CI checks out with ``submodules: false``, so the data trees may be absent;
nothing here requires them to be present.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tool_poetry() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["tool"]["poetry"]


def test_package_include_is_shallow_glob() -> None:
    assert _tool_poetry()["packages"] == [{"include": "nichebench/*", "from": "src"}]


def test_data_trees_excluded_as_directories() -> None:
    exclude = _tool_poetry()["exclude"]
    assert "src/nichebench/frameworks/*/data" in exclude
    # `/**` re-walks every matched subtree during exclude discovery.
    assert not any(pattern.endswith("/**") for pattern in exclude)


def test_exclude_patterns_resolve_against_checkout() -> None:
    tool_poetry = _tool_poetry()
    checked_out = set(REPO_ROOT.glob("src/nichebench/frameworks/*/data"))
    matched = {
        path for pattern in tool_poetry["exclude"] for path in REPO_ROOT.glob(pattern)
    }
    assert matched >= checked_out
