"""Runtime hint resolution and injection.

This module owns the **mechanics** of locating and copying runtime hint files.
It intentionally does **not** own hint content, hint semantics, or how the agent
interprets hints — those concerns belong to the task definition and the agent
itself.

Module-level responsibilities
-----------------------------
* **Resolution**: Locate a hints file via an explicit config path or a narrow
  manifest-relative fallback (``tasks/HINTS.md``).  No other paths are searched.
* **Injection**: Copy a resolved hints file into the workspace root as
  ``HINTS.md`` when ``runtime_hints_enabled`` is set.
* **Validation**: Raise a caller-supplied exception when hints are enabled but
  no file exists, or when the file is empty.  The module does not inspect or
  rewrite hint content.

What this module does *not* own
-------------------------------
* Hint content or meaning — written by the task author.
* How the agent receives or acts on hints — handled by the agent runtime.
* Task-specific ``HINTS.md`` files alongside individual manifests — those are
  task-internal assets resolved by the task loader, not global hints.
* Defaulting ``runtime_hints_enabled`` — decided by the caller or config.

Operational constraints
-----------------------
* The explicit config path is resolved relative to :py:func:`Path.cwd` when
  relative, so callers should ensure CWD is the workspace root or an
  equivalent stable location.
* The manifest-relative fallback only fires when the manifest lives at
  ``tasks/manifest/<name>.yaml`` — a layout enforced by the task discovery
  layer.  This keeps the fallback predictable and avoids accidental matches
  in unrelated directories.
* Injection is a **copy**, not a move or symlink.  The source file is not
  modified or consumed.
* The written file is always UTF-8 with a trailing newline.

Calling conventions
-------------------
Callers should:
1. Check ``runtime_hints_enabled`` in ``evaluation_config`` before calling
   :py:func:`inject_runtime_hints`.
2. Supply a descriptive ``validation_error_cls`` (e.g. a custom
   ``ValidationError``) so errors surface with meaningful context.
3. Treat the returned ``Path`` as informational — the module does not retain
   state after injection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from nichebench.core.datamodel import TestCaseSpec


def resolve_runtime_hints_file(
    test_case: TestCaseSpec,
    evaluation_config: dict[str, Any],
) -> Optional[Path]:
    """Resolve the path to the global runtime hints file, if any.

     Resolution order
     ----------------
     1. **Explicit config** — ``evaluation_config["runtime_hints_file"]`` is
        checked first.  Relative paths are resolved against :py:func:`Path.cwd`.
        If the path does not exist, it is treated as absent (returns ``None``)
        rather than causing an error, because the caller may prefer to fall back
        silently rather than fail.
     2. **Manifest-relative fallback** — when no explicit path is configured
        and the manifest lives at the canonical ``tasks/manifest/<name>.yaml``
        location, the harness looks for ``tasks/HINTS.md`` beside the manifest
        directory.  This matches the structure used by the runtime pack.
        Returns ``None`` if the manifest is in any other location.

     Returns
     -------
    Resolved absolute :py:class:`Path` to the hints file, or ``None`` when no
     hints file is configured and the manifest is not in the canonical location
     or the fallback file does not exist.

     Notes
     -----
     This function performs no I/O beyond checking path existence.  It does not
     read or validate the file contents — that is deferred to
     :py:func:`inject_runtime_hints`.
    """
    configured = evaluation_config.get("runtime_hints_file")
    if configured:
        path = Path(str(configured))
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.resolve() if path.exists() else None

    if not test_case.file_path:
        return None
    manifest_path = Path(test_case.file_path)
    if manifest_path.parent.name != "manifest" or manifest_path.parent.parent.name != "tasks":
        return None

    hints_path = manifest_path.parent.parent / "HINTS.md"
    return hints_path if hints_path.exists() else None


def inject_runtime_hints(
    workspace_path: Path,
    test_case: TestCaseSpec,
    evaluation_config: dict[str, Any],
    validation_error_cls: type[Exception],
) -> Optional[Path]:
    """Copy the global runtime hints file into the workspace root.

     This function is a no-op when ``runtime_hints_enabled`` is ``False`` or
     absent from ``evaluation_config``.

     When hints are enabled, the function follows a two-step contract:

     1. **Resolve** — calls :py:func:`resolve_runtime_hints_file` to locate the
        hints file.  If resolution returns ``None``, a
        ``validation_error_cls`` is raised immediately.
     2. **Inject** — reads the source file, strips trailing whitespace, appends
        a single newline, and writes the result to ``<workspace_path>/HINTS.md``.
        An empty file after stripping causes a ``validation_error_cls`` to be
        raised, because an empty hints file is almost always a configuration
        mistake.

     Parameters
     ----------
     workspace_path
         Absolute path to the run workspace root.  The harness creates this
         directory before calling this function.
     test_case
         The :py:class:`TestCaseSpec` for the current task.  Used only for error
         messaging (``test_case.id``) and for delegating to
         :py:func:`resolve_runtime_hints_file`.
     evaluation_config
         Configuration dict.  Must contain ``runtime_hints_enabled`` (bool) to
         activate hints.  Optionally contains ``runtime_hints_file`` (str) to
         override the default hints path.
     validation_error_cls
         Exception type to raise on validation failures.  The harness supplies
         a descriptive subclass so errors are catchable and display meaningfully
         in the CLI.

     Returns
     -------
    The resolved source :py:class:`Path` on success, ``None`` when hints are
     disabled.

     Raises
     ------
     validation_error_cls
         When hints are enabled but :py:func:`resolve_runtime_hints_file`
         returns ``None``, or when the hints file is empty after stripping.

     Operational notes
     -----------------
     * The written file is always UTF-8 with an explicit trailing newline, so
       text editors and agents see a well-formed file without irregular EOF.
     * The source file is read-only; no writes are performed on the original.
     * This function does not cache state.  Each call reads the source file
       fresh, which is appropriate because runtime hint files are static for
       the duration of a run.
    """
    if not evaluation_config.get("runtime_hints_enabled", False):
        return None

    hints_path = resolve_runtime_hints_file(test_case, evaluation_config)
    if hints_path is None:
        raise validation_error_cls(f"Runtime hints enabled but no hints file found for {test_case.id}")

    hints = hints_path.read_text(encoding="utf-8").strip()
    if not hints:
        raise validation_error_cls(f"Runtime hints enabled but hints file is empty: {hints_path}")

    (workspace_path / "HINTS.md").write_text(hints + "\n", encoding="utf-8")
    return hints_path
