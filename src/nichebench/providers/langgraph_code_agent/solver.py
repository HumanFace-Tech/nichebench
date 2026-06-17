"""Solver node creation for the LangGraph code agent.

The solver node executes one step of the plan using the LLM with full
prior-step context. After execution it extracts created-file references
and a per-step summary, then returns control to the Planner for the next
step or finalisation.

Ownership
=========
This module is owned by the ``langgraph_code_agent`` package. It depends
on ``state.py`` for the ``PlannerState`` type and on ``extraction.py``
for the ``extract_summary`` and ``extract_filenames`` helpers.

Failure handling
================
If a step raises an exception the node still advances the step index
(to avoid an infinite loop) and records the error string in
``step_outputs``.
"""

import logging
from typing import Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_litellm import ChatLiteLLM

from .extraction import extract_filenames, extract_summary
from .state import PlannerState

logger = logging.getLogger(__name__)


def create_solver_node(
    llm: ChatLiteLLM, progress_callback: Optional[Callable[[str, int], None]] = None
) -> Callable[[PlannerState], PlannerState]:
    """Create the Solver node that executes individual tasks.

    Args:
        llm: configured ``ChatLiteLLM`` instance used for LLM calls.
        progress_callback: optional ``(message, step)`` callable for UI
            progress updates. The ``step`` value is always ``1``.

    Returns:
        A node function suitable for adding to a ``StateGraph``.
    """

    def solver_node(state: PlannerState) -> PlannerState:
        """Solver executes the current step using plain text output."""
        current_step_index = state["current_step_index"]
        if current_step_index >= len(state.get("plan", [])):
            logger.info("🏁 Solver: No more steps to execute")
            return state  # Nothing to solve

        current_step = state["plan"][current_step_index]
        logger.info(f"⚡ Solver executing step {current_step_index + 1}: {current_step}")
        if progress_callback:
            progress_callback(f"⚡ Step {current_step_index + 1}/{len(state['plan'])}: {current_step[:50]}...", 1)

        # Import the specialized solver prompt
        from nichebench.frameworks.drupal.prompts.CODE_AGENT import (
            CODE_AGENT_SOLVER_PROMPT,
            CODE_AGENT_SOLVER_REQUEST_TEMPLATE,
        )

        solver_request_template = CODE_AGENT_SOLVER_REQUEST_TEMPLATE or ""

        # Build context from previous steps
        context_parts: list[str] = []
        if state.get("step_summaries"):
            context_parts.append("PREVIOUS STEP SUMMARIES:")
            for i, summary in enumerate(state["step_summaries"], 1):
                context_parts.append(f"{i}. {summary}")
            context_parts.append("")

        if state.get("created_files"):
            context_parts.append("FILES CREATED SO FAR:")
            for filename in state["created_files"]:
                context_parts.append(f"- {filename}")
            context_parts.append("")

        context_text = "\n".join(context_parts) if context_parts else "No previous steps completed yet."

        # Create simple solver prompt
        solver_messages = [
            SystemMessage(content=CODE_AGENT_SOLVER_PROMPT),
            HumanMessage(
                content=solver_request_template.format(
                    original_task=state["original_task"],
                    context_block=(f"CONTEXT: {state['context']}" if state.get("context") else ""),
                    plan_text="\n".join(f"{i + 1}. {step}" for i, step in enumerate(state["plan"])),
                    step_number=current_step_index + 1,
                    current_step=current_step,
                    context_text=context_text,
                )
            ),
        ]

        try:
            logger.info(f"🤖 LLM execution for step {current_step_index + 1}")

            result = llm.invoke(solver_messages)
            step_output = result.content if hasattr(result, "content") else str(result)

            # Ensure step_output is a string
            if isinstance(step_output, list) or not isinstance(step_output, str):
                step_output = str(step_output)

            logger.info(f"✅ Step {current_step_index + 1} execution completed")

            # Extract summary and filenames from output
            summary = extract_summary(step_output)
            filenames = extract_filenames(step_output)

            # Update state with new data
            new_step_summaries = list(state.get("step_summaries", []))
            new_step_summaries.append(summary)

            new_created_files = list(state.get("created_files", []))
            new_created_files.extend(filenames)

            new_step_outputs = list(state.get("step_outputs", []))
            new_step_outputs.append(step_output)

            next_step_index = current_step_index + 1

            return {
                **state,
                "current_step_index": next_step_index,
                "step_summaries": new_step_summaries,
                "created_files": new_created_files,
                "step_outputs": new_step_outputs,
            }

        except Exception as e:
            logger.error(f"❌ Step {current_step_index + 1} failed: {e}")

            # Add error summary
            error_summary = f"Step failed: {str(e)}"
            new_step_summaries = list(state.get("step_summaries", []))
            new_step_summaries.append(error_summary)

            new_step_outputs = list(state.get("step_outputs", []))
            new_step_outputs.append(f"ERROR: {str(e)}")

            return {
                **state,
                "current_step_index": current_step_index + 1,  # Still advance to avoid infinite loop
                "step_summaries": new_step_summaries,
                "step_outputs": new_step_outputs,
            }

    return solver_node
