"""Runtime execution components.

Runtime-specific helpers extracted from the orchestration layer in
small, behavior-preserving steps.

Package overview
================
This package owns the runtime-task execution pipeline for ``drupal_runtime``
tasks. It is not a general-purpose library; all public interfaces are
designed for consumption by the execution layer
(:class:`RuntimeExecutionMixin <nichebench.execution.runtime.executor.mixin.RuntimeExecutionMixin>`).

Subpackages and modules
-----------------------
artifacts/
    Artifact lifecycle: redaction, validation-artifact extraction, tool-policy
    helpers, catastrophic-failure detection, and persistence to the results
    directory.  No workspace/DDEV lifecycle, no trajectory reconstruction,
    and no check execution lives here.

cage/
    Cage container execution: ``CageExecutionMixin`` (the only public entry
    point), docker-argument construction, subprocess launch/stream readers,
    inactivity watchdog, auto-retry logic for rejected tool attempts, and
    island path setup.  Container-safety constraints (read-only socket, run-
    scoped temp directories, blocked unsafe git operations) are documented
    in the package.

checks.py
    Check resolution: maps human-readable check references in a task manifest
    to fully-specified check dictionaries that
    :meth:`RuntimeScorer.run_deterministic_checks
    <nichebench.execution.runtime.scoring.scorer.RuntimeScorer.run_deterministic_checks>`
    can execute.  Resolution order is documented in the module docstring.

executor/
    Runtime executor mixin and high-level orchestration flow.  Exposes
    ``RuntimeExecutionMixin`` as its sole public export.

hints.py
    Hint resolution and injection: locates a global hints file (explicit config
    path or manifest-relative fallback) and copies it into the workspace root
    as ``HINTS.md`` when ``runtime_hints_enabled`` is set.  Does not own hint
    content or agent-side interpretation.

image.py
    Cage image resolution and DDEV capability probing.  The single public entry
    point is :func:`resolve_effective_cage_image
    <nichebench.execution.runtime.image.resolve_effective_cage_image>`; all
    other functions are implementation details.  Does not own cage execution,
    workspace lifecycle, or Drupal setup.

metadata.py
    Assembles ``metadata.json`` for the result bundle: runtime configuration,
    model bindings, tool flags, and island topology.  Pure function; no file
    I/O beyond what the caller supplies.

opencode_config.py
    OpenCode cage configuration: prompt loading, model binding, provider API
    key injection, and ``opencode.json`` generation at runtime.  Provider
    remapping (non-native providers → openai-compatible with baseURL) is
    documented in the module.

preflight.py
    Host-side and workspace-side preflight validation.  ``run_runtime_preflight_host``
    validates the container image reference and tool availability before
    provisioning; ``run_runtime_preflight_workspace`` runs a smoke-check
    script after DDEV startup.  Does not own DDEV lifecycle or cage execution.

scoring/
    Deterministic checks, hybrid score aggregation, and validation.  Exports
    ``RuntimeScorer``, ``CheckResult``, ``HybridScore``, and ``ValidationError``.
    Check execution logic lives here; check definitions live in the task manifest.

trajectory/
    Trajectory capture and reconstruction from OpenCode session files or
    SQLite.  Exports normalise, session discovery, polling, and debug-dump
    helpers.  Best-effort; malformed input returns empty/partial results rather
    than raising.

workspace/
    DDEV-backed isolated workspace lifecycle.  Exports ``Workspace``,
    ``WorkspaceError``, and ``DDEVError``.  All other modules are internal
    implementation details.

wrappers/
    Cage wrapper helpers.  Currently exports only ``write_cage_git_wrapper``.
    Shell scripts live in the ``scripts/`` subdirectory.

What this package does NOT own
-----------------------------
- Task discovery, loading, or manifest parsing — see ``nichebench.core``.
- CLI, config loading, or profile resolution — see ``nichebench.config``.
- The LangGraph code agent or conversation manager — see ``nichebench.providers``.
- General scoring (quiz, code_gen, bug_fixing) — see ``nichebench.core.scoring``.
- Static Drupal tasks (quiz, code_gen, bug_fixing) — see
  ``nichebench.frameworks.drupal``.

Calling conventions
------------------
- Import subpackage exports directly from the subpackage (e.g.
  ``from nichebench.execution.runtime.workspace import Workspace``) or from
  this package (e.g. ``from nichebench.execution.runtime import Workspace``).
  Both paths are supported.
- All public functions accept injectable ``subprocess_module`` and
  ``validation_error_cls`` arguments so they remain testable without a live
  Docker/DDEV environment.
- Functions that accept a ``TestCaseSpec`` use it for error messaging and path
  resolution; they do not retain state after the call.
- The execution layer is the sole caller of most functions in this package.
  External callers should treat all interfaces as stable but not call
  internal modules (those not listed in ``__all__``) directly.

Package-level constraints
-------------------------
- No module in this package may import ``nichebench.cli`` or ``nichebench.config``
  to avoid circular dependencies at the execution layer boundary.
- ``litellm.api_base`` is reset after every agent run; do not set it in any
  function that survives a single call.
- Workspace paths and DDEV project names are always derived from a task UUID;
  no hardcoded IDs or entity IDs in routing or access logic.
"""

from nichebench.execution.runtime import (
    artifacts,
    cage,
    checks,
    hints,
    image,
    metadata,
    opencode_config,
    preflight,
    trajectory,
    wrappers,
)

__all__ = [
    "artifacts",
    "cage",
    "checks",
    "hints",
    "image",
    "metadata",
    "opencode_config",
    "preflight",
    "trajectory",
    "wrappers",
]
