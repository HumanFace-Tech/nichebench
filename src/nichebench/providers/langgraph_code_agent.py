"""LangGraph StateGraph for plan-based code generation.

This implements a clean two-node architecture:
1. Planner Node - Creates and supervises the execution plan
2. Solver Node - Executes individual tasks using plain text output

The graph maintains shared state and provides efficient step-by-step execution.
"""

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_litellm import ChatLiteLLM
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


class PlannerState(TypedDict):
    """State maintained across nodes in the LangGraph execution."""

    original_task: str
    context: Optional[str]
    plan: List[str]
    current_step_index: int
    step_summaries: List[str]
    created_files: List[str]
    step_outputs: List[str]
    is_complete: bool
    final_result: Optional[str]


def create_planner_node(llm: ChatLiteLLM, progress_callback: Optional[Callable[[str, int], None]] = None):
    """Create the Planner node that manages the overall execution."""

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
            )

            # Use the actual CODE_AGENT planner prompt with simplified output format instruction
            planning_messages = [
                SystemMessage(content=CODE_AGENT_PLANNER_PROMPT),
                HumanMessage(
                    content=f"""
Task: {state['original_task']}
{f"Context: {state['context']}" if state.get('context') else ""}

IMPORTANT: Reply with ONLY the numbered steps from your plan.
Do NOT include the canonical identifiers section or any other text.
Just give me the numbered steps like:

1. Create module info file
2. Create service class
3. Add routing configuration
4. Create controller
5. Add tests
"""
                ),
            ]

            try:
                logger.info("🚀 Calling LLM for planning...")
                response = llm.invoke(planning_messages)
                plan_text = response.content if hasattr(response, "content") else str(response)

                # Parse plan steps - extract only numbered steps from CODE_AGENT response
                plan_steps = []
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
                    if in_numbered_section and (
                        line.startswith("PLANNING RULES")
                        or line.startswith("Your response")
                        or line.startswith("OUTPUT FORMAT")
                    ):
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
            parts = []

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


def create_solver_node(llm: ChatLiteLLM, progress_callback: Optional[Callable[[str, int], None]] = None):
    """Create the Solver node that executes individual tasks."""

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
        )

        # Build context from previous steps
        context_parts = []
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
                content=f"""
ORIGINAL TASK: {state['original_task']}
{f"CONTEXT: {state['context']}" if state.get('context') else ""}

FULL IMPLEMENTATION PLAN:
{chr(10).join(f"{i+1}. {step}" for i, step in enumerate(state['plan']))}

CURRENT ASSIGNMENT (FOCUS ON THIS ASSIGNMENT!):
You are responsible for step {current_step_index + 1}: {current_step}

{context_text}

Execute ONLY step {current_step_index + 1}. Provide your response in this exact format:

EXPLANATION:
[Explain what you're doing for this step]

CHANGES:
[List all files you're creating/modifying and their content]

SUMMARY:
[Brief summary of what was accomplished in this step]
"""
            ),
        ]

        try:
            logger.info(f"🤖 LLM execution for step {current_step_index + 1}")

            result = llm.invoke(solver_messages)
            step_output = result.content if hasattr(result, "content") else str(result)

            # Ensure step_output is a string
            if isinstance(step_output, list):
                step_output = str(step_output)
            elif not isinstance(step_output, str):
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


def extract_summary(text: str) -> str:
    """Extract the SUMMARY section from solver output."""
    pattern = r"SUMMARY:\s*\n(.*?)(?:\n\n|\n[A-Z]+:|$)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "Step completed (no summary found)"


def extract_filenames(text: str) -> List[str]:
    """Extract filenames from CHANGES section of solver output."""
    filenames = []

    # Look for patterns like "FILENAME: path/to/file.ext" or "File: path/to/file.ext"
    filename_patterns = [
        r"FILENAME:\s*([^\n]+)",
        r"File:\s*([^\n]+)",
        r"Creating:\s*([^\n]+)",
        r"Modifying:\s*([^\n]+)",
    ]

    for pattern in filename_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            filename = match.strip()
            if filename and filename not in filenames:
                filenames.append(filename)

    return filenames


def should_continue(state: PlannerState) -> Literal["solver", "planner", "__end__"]:
    """Determine the next step in the graph."""

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
    """
    LangGraph-based code generation agent using Planner + Solver architecture.
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

    def _create_llm(self) -> ChatLiteLLM:
        """Create LiteLLM client with optimized parameters."""
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

    def _build_graph(self, progress_callback: Optional[Callable[[str, int], None]] = None):
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
        """Execute a task using the LangGraph agent."""

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
