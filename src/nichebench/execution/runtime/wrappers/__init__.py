"""Cage wrapper helpers.

Shell scripts live in the ``scripts/`` subdirectory as real .sh files
with syntax highlighting and shellcheck support.
"""

from nichebench.execution.runtime.wrappers._write import write_cage_git_wrapper

__all__ = ["write_cage_git_wrapper"]
