"""LangGraphCodeAgent facade and graph routing.

This module provides the main public interface for the plan-based code
generation agent. It wires together the planner and solver nodes into
a ``StateGraph`` and exposes ``LangGraphCodeAgent.execute_task``.

Ownership
=========
This module is owned by the ``langgraph_code_agent`` package. It is the
only module that should be imported by external callers (e.g.
``mut.py``). All other modules (``state.py``, ``planner.py``, ``solver.py``,
``extraction.py``) are internal implementation details.

litellm.api_base reset contract
===============================
``ChatLiteLLM`` (used inside the solver node) may set ``litellm.api_base``
as a global during execution. The caller of ``execute_task`` is responsible
for resetting this global after the call returns to prevent cross-
contamination of subsequent judge LLM calls. See ``mut.py`` for the
caller-side reset logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, Literal, Optional

if TYPE_CHECKING:
    from langchain_litellm import ChatLiteLLM

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .planner import create_planner_node
from .solver import create_solver_node
from .state import PlannerState

logger = logging.getLogger(__name__)


def should_continue(state: PlannerState) -> Literal["solver", "planner", "__end__"]:
    """Routing function for LangGraph conditional edges.

    Called after both the planner and solver nodes. Routing logic:

    * ``is_complete`` set → ``__end__``
    * more steps in the plan → ``solver``
    * no more steps but not marked complete → ``planner`` (triggers
      finalisation in the planner node)
    """

    # Check if completed
    if state.get("is_complete"):
        logger.info("🎯 Graph execution complete")
        return "__end__"

    # Check if we need to go to solver
    if state["current_step_index"] < len(state.get("plan", [])):
        logger.info(f"🔄 Going to solver for step {state['current_step_index'] + 1}")
        return "solver"

    # Otherwise back to planner for finalization
    logger.info("🔄 Going back to planner for finalization")
    return "planner"


class LangGraphCodeAgent:
    """High-level interface for plan-based code generation via LangGraph.

    Construct with the model identifier and optional per-model parameters,
    then call :meth:`execute_task` with a task description and optional
    context. The method blocks until the graph finishes and returns the
    concatenated final result (plan + all step outputs + created-file list).

    The LLM is created once during ``__init__`` and reused across calls;
    ``thread_id`` is fixed to ``"main_execution"`` so a single run's state
    is coherent. Multiple concurrent tasks should use separate
    ``LangGraphCodeAgent`` instances.
    """

    def __init__(
        self,
        model: str = "groq/llama-3.1-8b-instant",
        max_retries: int = 3,
        tool_choice: str = "auto",
        frequency_penalty: float = 0.2,
        custom_llm_params: Optional[Dict[str, Any]] = None,
    ):
        """Initialize with improved Groq model parameters."""
        self.model = model
        self.max_retries = max_retries
        self.tool_choice = tool_choice
        self.frequency_penalty = frequency_penalty
        self.custom_llm_params = custom_llm_params or {}

        # Initialize LLM
        self.llm = self._create_llm()

    def _create_llm(self) -> "ChatLiteLLM":
        """Create LiteLLM client with optimized parameters.

        ChatLiteLLM is looked up from the package namespace at call time
        (not at module load time) so that test patches on the package
        namespace are respected.
        """
        # Late import from package namespace to respect test patches
        import nichebench.providers.langgraph_code_agent as pkg

        ChatLiteLLM = pkg.ChatLiteLLM
        llm_params = self._prepare_llm_params()
        return ChatLiteLLM(**llm_params)

    def _prepare_llm_params(self) -> Dict[str, Any]:
        """Prepare LLM parameters with Groq optimizations."""
        base_params = {
            "model": self.model,
            "max_retries": self.max_retries,
            "timeout": 120.0,
            "temperature": 0.1,
        }

        # Add Groq-specific parameters for better JSON parsing
        if "groq/" in self.model:
            base_params.update(
                {
                    "tool_choice": self.tool_choice,
                    "frequency_penalty": self.frequency_penalty,
                    "presence_penalty": 0.0,
                    "top_p": 0.9,
                    "max_tokens": 4000,
                }
            )

        # Apply custom parameters (these override defaults)
        base_params.update(self.custom_llm_params)

        return base_params

    def _build_graph(self, progress_callback: Optional[Callable[[str, int], None]] = None) -> Any:
        """Build the LangGraph StateGraph."""
        workflow = StateGraph(PlannerState)

        # Add nodes
        workflow.add_node("planner", create_planner_node(self.llm, progress_callback))
        workflow.add_node("solver", create_solver_node(self.llm, progress_callback))

        # Set entry point
        workflow.add_edge(START, "planner")

        # Add conditional routing
        workflow.add_conditional_edges(
            "planner", should_continue, {"solver": "solver", "planner": "planner", "__end__": END}
        )

        workflow.add_conditional_edges(
            "solver", should_continue, {"planner": "planner", "solver": "solver", "__end__": END}
        )

        # Use in-memory checkpointer for state persistence
        checkpointer = InMemorySaver()
        return workflow.compile(checkpointer=checkpointer)

    def execute_task(
        self,
        task_description: str,
        context: Optional[str] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ) -> str:
        """Run the Planner/Solver graph for the given task.

        Args:
            task_description: the original user-facing task prompt.
            context: optional background / environment context to prepend
                to every LLM call.
            progress_callback: optional ``(message, step)`` callable for
                UI progress updates. The ``step`` value is always ``1``
                (placeholder for nested step reporting).

        Returns:
            A multi-section text containing the execution plan, all step
            outputs, files created, and step summaries. This string is
            designed to be passed directly to the judge for evaluation.
        """

        # Build the graph
        app = self._build_graph(progress_callback)

        # Initialize state
        initial_state: PlannerState = {
            "original_task": task_description,
            "context": context,
            "plan": [],
            "current_step_index": 0,
            "step_summaries": [],
            "created_files": [],
            "step_outputs": [],
            "is_complete": False,
            "final_result": None,
        }

        # Execute the graph
        config = RunnableConfig(configurable={"thread_id": "main_execution"})
        final_state = app.invoke(initial_state, config=config)

        # Return the final result
        return final_state.get("final_result", "No result generated")
