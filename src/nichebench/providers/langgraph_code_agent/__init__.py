"""LangGraph plan-based code generation agent.

Package structure
================
``langgraph_code_agent/``
  ``__init__.py``   — public API exports (LangGraphCodeAgent, should_continue,
                     PlannerState)
  ``agent.py``      — LangGraphCodeAgent facade and should_continue routing
  ``planner.py``    — planner node creation and plan-step parsing
  ``solver.py``     — solver node creation and step execution
  ``extraction.py`` — extract_summary and extract_filenames helpers
  ``state.py``      — PlannerState TypedDict definition

Public API
==========
All external callers should import from this package root only::

    from nichebench.providers.langgraph_code_agent import (
        LangGraphCodeAgent,
        should_continue,
        PlannerState,
    )

Do not import directly from sub-modules (``planner.py``, ``solver.py``, etc.)
as those are internal implementation details.

litellm.api_base contract
========================
``ChatLiteLLM`` sets ``litellm.api_base`` as a global during LangGraph
execution. The caller of ``LangGraphCodeAgent.execute_task`` must reset
this global after the call returns (set to ``None``). See ``mut.py`` for
the caller-side reset logic. Failing to reset causes subsequent judge
LLM calls to route to the wrong endpoint.
"""

from langchain_litellm import ChatLiteLLM  # noqa: F401  # backwards-compat test patches

from .agent import LangGraphCodeAgent, should_continue
from .state import PlannerState

__all__ = [
    "LangGraphCodeAgent",
    "should_continue",
    "PlannerState",
]
