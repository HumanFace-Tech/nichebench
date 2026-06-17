"""Runtime metadata assembly.

This module is responsible **only** for constructing the ``metadata.json``
artifact written into the runtime result bundle. It captures the runtime
configuration, model bindings, tool flags, and island topology that were in
effect for a single task execution.

What this module does *not* own
--------------------------------
- Workspace setup or teardown — see :mod:`nichebench.execution.runtime.workspace`
- Trajectory or trace capture — see :mod:`nichebench.execution.runtime.trajectory`
- Deterministic checks — see :mod:`nichebench.execution.runtime.checks`
- Artifact persistence — see :mod:`nichebench.execution.runtime.artifacts`
- Image resolution or DDEV capability checks — see
  :mod:`nichebench.execution.runtime.image`

Callers
-------
``build_runtime_metadata`` is called **once per task** from
:meth:`RuntimeExecutor._build_runtime_metadata
<nichebench.execution.runtime.executor.mixin.RuntimeExecutor._build_runtime_metadata>`
and its result is stored under the ``metadata.json`` key in
``result.runtime_artifacts``. The dict is passed to the scoring pipeline
but this module has no role in scoring itself.

Operational constraints
-----------------------
- ``test_case`` and ``workspace`` are accepted for future extensibility but are
  not consulted during assembly; they are deleted without side effects.
- ``effective_image`` is used only when it differs from ``runtime_container_image_base``;
  when it is ``None`` the base image name is used as the effective value.
- ``island_topology``, ``retry_info``, and ``review_pass_info`` are optional
  and omitted from the dict entirely when ``None`` — callers that need to
  detect presence should check ``"key" in metadata`` rather than
  ``metadata.get("key")``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec


def build_runtime_metadata(
    test_case: TestCaseSpec,
    profile: Any,
    runtime_mode: str,
    runtime_config: Dict[str, Any],
    workspace: Any,
    mut_model_config: Dict[str, Any],
    cli_model_override: Optional[str],
    compute_opencode_model_binding: Callable[[str, str, Dict[str, Any], Optional[str]], Tuple[str, str]],
    island_topology: Optional[Dict[str, Any]] = None,
    effective_image: Optional[str] = None,
    retry_info: Optional[Dict[str, Any]] = None,
    review_pass_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the runtime metadata dict for a single task execution.

    The returned dict is written verbatim to ``metadata.json`` inside the
    result bundle. All keys described under *Return semantics* are always
    present; optional keys are omitted when their value is ``None``.

    Contract
    --------
    - **No side effects.** This function is pure — it does not write files,
      mutate inputs, or call external services.
    - **Deterministic for identical inputs.** Model binding and tool-flag
      values are derived entirely from the arguments; no global state is read.
    - ``test_case`` and ``workspace`` are accepted for interface symmetry with
      the caller but are **not used** internally.

    Arguments
    ---------
    test_case : TestCaseSpec
        The task test case spec. Accepted for forward compatibility; not
        consulted during assembly.
    profile : Any
        The active evaluation profile (e.g. :class:`~nichebench.config.profile.Profile`).
        Used only to read tool-flag defaults via ``getattr``.
    runtime_mode : str
        Raw runtime mode string from the executor (``"host"``, ``"cage"``,
        ``"container"``). ``"cage"`` and ``"container"`` are normalised to
        ``"cage"`` in the output; other values are passed through unchanged.
    runtime_config : Dict[str, Any]
        Full runtime configuration dict. Keys consulted:

        - ``runtime_container_image`` — base container image name
        - ``runtime_opencode_model`` — optional OpenCode model override
        - ``runtime_opencode_model_aliases`` — alias map passed through to
          ``compute_opencode_model_binding``
    workspace : Any
        Workspace object. Accepted for forward compatibility; not consulted.
    mut_model_config : Dict[str, Any]
        Model-under-test configuration dict. Must contain ``provider`` and
        ``model`` keys; both are cast to ``str``.
    cli_model_override : Optional[str]
        Raw value of the ``--model`` CLI flag, or ``None`` if not supplied.
        Passed to ``compute_opencode_model_binding`` where it takes
        precedence over ``runtime_opencode_model``.
    compute_opencode_model_binding : Callable
        Resolver that maps the MUT provider/model pair to the OpenCode agent's
        provider and model ID. Signature::

            (mut_provider: str,
             mut_model: str,
             runtime_config: Dict[str, Any],
             cli_model_override: Optional[str]) -> Tuple[str, str]

        See :func:`nichebench.execution.runtime.opencode_config.compute_opencode_model_binding`.
    island_topology : Optional[Dict[str, Any]], optional
        Island path-mapping topology set up by the cage executor.
        Omitted from output when ``None``.
    effective_image : Optional[str], optional
        Resolved image tag after DDEV auto-build or capability checks.
        Falls back to ``runtime_container_image`` when ``None``.
    retry_info : Optional[Dict[str, Any]], optional
        Retry state dict for the current run. Omitted from output when
        ``None``.
    review_pass_info : Optional[Dict[str, Any]], optional
        Two-pass nudge flow state for the current run. Omitted from output
        when ``None``.

    Return semantics
    ----------------
    Always present keys:

    ``effective_runtime_mode`` : str
        Normalised runtime mode (``"cage"`` or original value).
    ``runtime_mode`` : str
        Raw runtime mode argument.
    ``runtime_container_image_base`` : str
        Base image name from ``runtime_config`` (empty string if not set).
    ``runtime_container_image_effective`` : str
        Tag actually used (``effective_image`` or ``base_image``).
    ``mut_model_binding`` : str
        MUT binding in ``"provider/model"`` form.
    ``opencode_provider`` : str
        OpenCode agent provider.
    ``opencode_model_id`` : str
        OpenCode agent model identifier.
    ``opencode_model_binding`` : str
        OpenCode binding in ``"provider/model"`` form.
    ``tool_flags`` : Dict[str, bool]
        Snapshot of tool permission flags from the profile:

        - ``allow_web_search``
        - ``allow_browser``
        - ``allow_mcp``
        - ``allow_external_network_for_shell``

        Default is ``False`` for the first three and ``False`` for the last;
        the profile's ``getattr`` defaults are applied when the attribute is
        absent.

    Conditionally present keys (omitted when ``None``):

    ``island_topology`` : Dict[str, Any]
        Present when ``island_topology`` argument is not ``None``.
    ``retry_info`` : Dict[str, Any]
        Present when ``retry_info`` argument is not ``None``.
    ``review_pass_info`` : Dict[str, Any]
        Present when ``review_pass_info`` argument is not ``None``.

    Examples
    --------
    >>> from nichebench.execution.runtime.metadata import build_runtime_metadata
    >>> from nichebench.core.datamodel import TestCaseSpec
    >>> spec = TestCaseSpec(id="t001", type="drupal_runtime", raw={})
    >>> def binding(p, m, c, o): return (p, m)
    >>> meta = build_runtime_metadata(
    ...     test_case=spec,
    ...     profile=type("P", (), {})(),
    ...     runtime_mode="cage",
    ...     runtime_config={"runtime_container_image": "ddev/web:9"},
    ...     workspace=None,
    ...     mut_model_config={"provider": "groq", "model": "llama-3.3-70b"},
    ...     cli_model_override=None,
    ...     compute_opencode_model_binding=binding,
    ... )
    >>> meta["effective_runtime_mode"]
    'cage'
    >>> meta["mut_model_binding"]
    'groq/llama-3.3-70b'
    """
    del test_case, workspace
    effective_runtime_mode = "cage" if runtime_mode in ("cage", "container") else runtime_mode
    base_image = str(runtime_config.get("runtime_container_image", ""))
    metadata: Dict[str, Any] = {
        "effective_runtime_mode": effective_runtime_mode,
        "runtime_mode": runtime_mode,
        "runtime_container_image_base": base_image,
        "runtime_container_image_effective": effective_image or base_image,
    }

    mut_provider = str(mut_model_config.get("provider", ""))
    mut_model = str(mut_model_config.get("model", ""))
    metadata["mut_model_binding"] = f"{mut_provider}/{mut_model}"

    opencode_provider, opencode_model_id = compute_opencode_model_binding(
        mut_provider,
        mut_model,
        runtime_config,
        cli_model_override,
    )
    metadata["opencode_provider"] = opencode_provider
    metadata["opencode_model_id"] = opencode_model_id
    metadata["opencode_model_binding"] = f"{opencode_provider}/{opencode_model_id}"

    metadata["tool_flags"] = {
        "allow_web_search": getattr(profile, "allow_web_search", False),
        "allow_browser": getattr(profile, "allow_browser", False),
        "allow_mcp": getattr(profile, "allow_mcp", True),
        "allow_external_network_for_shell": getattr(profile, "allow_external_network_for_shell", False),
    }

    if island_topology:
        metadata["island_topology"] = island_topology
    if retry_info:
        metadata["retry_info"] = retry_info
    if review_pass_info:
        metadata["review_pass_info"] = review_pass_info

    return metadata
