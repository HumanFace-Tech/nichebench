"""Planner node creation for the LangGraph code agent.

The planner node is the "supervisor" in the planner/solver graph. It
handles three phases:

1. **Plan creation** — on the first invocation, it calls the LLM with
   the task description and parses a numbered step list.
2. **Completion check** — if all steps are done, it assembles the final
   result string and sets ``is_complete``.
3. **Delegation** — otherwise it returns the current state unchanged so
   the graph routes to the solver node.

Ownership
=========
This module is owned by the ``langgraph_code_agent`` package. It depends
on ``state.py`` for the ``PlannerState`` type and on ``extraction.py``
for any extraction logic (none currently used in this node).

Failure handling
================
If plan creation fails, the node falls back to a four-step default plan
and still completes successfully so the graph can terminate cleanly.
"""

import logging
from typing import Callable, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_litellm import ChatLiteLLM

from .state import PlannerState

logger = logging.getLogger(__name__)


def create_planner_node(
    llm: ChatLiteLLM, progress_callback: Optional[Callable[[str, int], None]] = None
) -> Callable[[PlannerState], PlannerState]:
    """Create the Planner node that manages the overall execution.

    Args:
        llm: configured ``ChatLiteLLM`` instance used for LLM calls.
        progress_callback: optional ``(message, step)`` callable for UI
            progress updates. The ``step`` value is always ``1``.

    Returns:
        A node function suitable for adding to a ``StateGraph``.
    """

    def planner_node(state: PlannerState) -> PlannerState:
        """Planner supervises the execution and delegates tasks to Solver."""
        logger.info(f"🧠 Planner executing - step {state['current_step_index']}/{len(state.get('plan', []))}")

        # Phase 1: Create plan if not exists
        if not state.get("plan"):
            logger.info("📋 Creating initial plan...")
            if progress_callback:
                progress_callback("📋 Creating execution plan...", 0)

            # Import the specialized planner prompt
            from nichebench.frameworks.drupal.prompts.CODE_AGENT import (
                CODE_AGENT_PLANNER_PROMPT,
                CODE_AGENT_PLANNER_REQUEST_TEMPLATE,
            )

            planner_request_template = CODE_AGENT_PLANNER_REQUEST_TEMPLATE or ""

            # Use the actual CODE_AGENT planner prompt with simplified output format instruction
            planning_messages = [
                SystemMessage(content=CODE_AGENT_PLANNER_PROMPT),
                HumanMessage(
                    content=planner_request_template.format(
                        original_task=state["original_task"],
                        context_block=(f"Context: {state['context']}" if state.get("context") else ""),
                    )
                ),
            ]

            try:
                logger.info("🚀 Calling LLM for planning...")
                response = llm.invoke(planning_messages)
                plan_text = response.content if hasattr(response, "content") else str(response)

                # Parse plan steps - extract only numbered steps from CODE_AGENT response
                plan_steps: List[str] = _parse_plan_steps(plan_text)

                if not plan_steps:
                    # Fallback plan - simple and effective
                    plan_steps = [
                        "Create module info and services configuration",
                        "Implement main classes and interfaces",
                        "Add configuration forms and routing",
                        "Create tests and documentation",
                    ]

                logger.info(f"Created plan with {len(plan_steps)} steps")
                if progress_callback:
                    progress_callback(f"📋 Plan created: {len(plan_steps)} steps", 1)
                return {
                    **state,
                    "plan": plan_steps,
                    "current_step_index": 0,
                    "step_summaries": [],
                    "created_files": [],
                    "step_outputs": [],
                }

            except Exception as e:
                logger.error(f"Planning failed: {e}")
                return {
                    **state,
                    "plan": [],
                    "current_step_index": 0,
                    "is_complete": True,
                    "final_result": f"Planning failed: {str(e)}",
                }

        # Phase 2: Check if we're done
        if state["current_step_index"] >= len(state["plan"]):
            logger.info("All steps completed, finalizing...")
            if progress_callback:
                progress_callback("🎯 Finalizing solution...", 1)

            # Build final solution with ALL generated content for judge evaluation
            parts: List[str] = []

            # Add execution plan first
            if state.get("plan"):
                parts.append("=== EXECUTION PLAN ===")
                for i, step in enumerate(state["plan"], 1):
                    parts.append(f"{i}. {step}")
                parts.append("")

            # Include all step outputs
            if state.get("step_outputs"):
                parts.append("=== STEP OUTPUTS ===")
                for i, output in enumerate(state["step_outputs"], 1):
                    parts.append(f"--- STEP {i} OUTPUT ---")
                    parts.append(output)
                    parts.append("")

            # Include created files summary
            if state.get("created_files"):
                parts.append("=== FILES CREATED ===")
                for filename in state["created_files"]:
                    parts.append(f"- {filename}")
                parts.append("")

            # Include step summaries
            if state.get("step_summaries"):
                parts.append("=== STEP SUMMARIES ===")
                for i, summary in enumerate(state["step_summaries"], 1):
                    parts.append(f"{i}. {summary}")
                parts.append("")

            # Build comprehensive solution for judge evaluation
            final_solution = "\n".join(parts) if parts else "No solution was generated."

            return {**state, "final_result": final_solution, "is_complete": True}

        # Phase 3: Continue to next step (will go to Solver)
        current_step = state["plan"][state["current_step_index"]]
        logger.info(f"Planning delegation of step: {current_step}")

        return state

    return planner_node


def _parse_plan_steps(plan_text: str) -> List[str]:
    """Parse numbered steps from raw LLM plan output.

    Extracts steps from a ``N) ...`` pattern inside a "numbered steps"
    section. Lines shorter than 10 characters after the number are
    filtered out as noise.

    Args:
        plan_text: raw LLM response string.

    Returns:
        List of step description strings (may be empty).
    """
    plan_steps: List[str] = []
    lines = str(plan_text).strip().split("\n")
    in_numbered_section = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Look for the start of numbered steps section
        if line.lower().startswith("2) numbered steps:") or "numbered steps" in line.lower():
            in_numbered_section = True
            continue

        # Stop if we hit another section after numbered steps
        if in_numbered_section and (line.startswith(("PLANNING RULES", "Your response", "OUTPUT FORMAT"))):
            break

        # Extract numbered steps
        if in_numbered_section and line and line[0].isdigit() and ". " in line[:5]:
            step = line.split(". ", 1)[1].strip()
            if step and len(step) > 10:  # Ensure meaningful content
                plan_steps.append(step)
        # Also catch direct numbered steps at the start if no section header
        elif not in_numbered_section and line and line[0].isdigit() and ". " in line[:5]:
            step = line.split(". ", 1)[1].strip()
            if step and len(step) > 10:
                plan_steps.append(step)

    return plan_steps
