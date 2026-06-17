"""Runtime workspace package — DDEV-backed isolated workspace lifecycle.

This package is a refactored split of the original ``workspace.py`` module.
The public API is preserved via re-exports in this file.

Public API (backward-compatible import path)
-------------------------------------------
``from nichebench.execution.runtime.workspace import Workspace, WorkspaceError, DDEVError``

All other modules (``model.py``, ``cleanup.py``, ``ddev.py``,
``static_analysis.py``, ``diff.py``) are internal implementation details.

Module ownership
----------------
- ``model.py`` — Workspace class and exceptions (public facade)
- ``cleanup.py`` — Docker/container cleanup helpers
- ``ddev.py`` — DDEV start/stop/health operations
- ``static_analysis.py`` — phpstan/composer.json patching
- ``diff.py`` — git diff and log capture
"""

from nichebench.execution.runtime.workspace.model import (
    DDEVError,
    Workspace,
    WorkspaceError,
)

__all__ = ["Workspace", "WorkspaceError", "DDEVError"]
