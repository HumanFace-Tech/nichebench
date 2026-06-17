"""MUT (Model Under Test) runner: executes a test case against the model under test.

This module is the entry point for running a single benchmark test case
against the model configured as the *model under test* (MUT).

Responsibilities
===============
* Route each test case to the appropriate execution strategy:
  single-turn (quiz, code generation), multi-turn (bug fixing), or
  plan-based agentic (code agent / runtime).
* Compose the user prompt from :class:`TestCaseSpec` using
  :class:`MUTPromptComposer` — task-specific prompting logic lives there,
  **not here**.
* Return ``(mut_output, user_input)`` where ``user_input`` is the composed
  prompt logged for traceability.

Key boundaries
==============
* This runner does **not** call the judge — that is the
  :class:`JudgeRunner`'s responsibility.
* Task-specific prompt construction is delegated to ``MUTPromptComposer``;
  adding a new task type usually means updating that class, not this one.
* The ``code_agent`` and ``runtime`` categories use
  :class:`LangGraphCodeAgent`, which manages its own state machine and
  **must** have ``litellm.api_base`` reset after each invocation
  (global state set by the LangGraph ``ChatLiteLLM`` wrapper would otherwise
  cause 404 errors on subsequent judge calls).
"""

from typing import Any, Dict, List, Optional, Tuple

from nichebench.core.datamodel import TestCaseSpec
from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.mut_prompt_composer import MUTPromptComposer


class MUTRunner:
    """Executes the MUT for a single test case.

    Owns a :class:`LiteLLMClient` (shared with the judge runner at the
    executor level) and routes to:

    * :meth:`_run_single_turn` — one prompt, one response (quiz, code gen,
      fallback for unknown categories).
    * :meth:`_run_multi_turn` — iterative conversation with the model,
      used for ``bug_fixing`` and ``code_agent``/``runtime`` categories.

    Lifecycle note: after a ``code_agent``/``runtime`` run the global
    ``litellm.api_base`` is reset because ``ChatLiteLLM`` sets it during
    LangGraph execution.  Failing to reset causes subsequent judge calls
    to route to the wrong endpoint.
    """

    def __init__(
        self, model_str: str, model_config: Dict[str, Any], timeout: int, retry_attempts: int, retry_delay: int
    ):
        self.model_str = model_str
        self.model_config = model_config
        self.client = LiteLLMClient(timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay)

    def run_test(
        self, test_case: TestCaseSpec, system_prompt: Optional[str], category: str, runner=None
    ) -> Tuple[str, str]:
        """Execute the MUT for a test case.

        Args:
            test_case: the task specification loaded from the framework YAML.
            system_prompt: optional system-level instruction overlay.
            category: one of ``quiz``, ``code_generation``, ``bug_fixing``,
                ``code_agent``, ``runtime``.
            runner: optional progress-reporting callback (used by the
                LangGraph agent to surface step updates in the CLI).

        Returns:
            Tuple of ``(mut_output, user_input)`` where ``user_input`` is
            the composed prompt sent to the model (for logging / traceability).
        """
        if category == "quiz" or category == "code_generation":
            return self._run_single_turn(test_case, system_prompt, category)
        if category in ("code_agent", "bug_fixing"):
            return self._run_multi_turn(test_case, system_prompt, category, runner)
        return self._run_single_turn(test_case, system_prompt, category)

    def _run_single_turn(self, test_case: TestCaseSpec, system_prompt: Optional[str], category: str) -> Tuple[str, str]:
        """Send one composed prompt and return the model's response.

        Used for ``quiz`` and ``code_generation`` categories, and as the
        fallback for any unrecognised category.
        """
        user_input = MUTPromptComposer.compose_prompt(
            test_case=test_case, system_prompt=system_prompt, category=category
        )

        mut_response = self.client.generate(
            prompt=user_input, model=self.model_str, model_params=self.model_config.get("parameters", {})
        )

        output = mut_response.get("output", f"[Error: no output from {self.model_str}]")
        return output, user_input

    def _run_multi_turn(
        self, test_case: TestCaseSpec, system_prompt: Optional[str], category: str, runner=None
    ) -> Tuple[str, str]:
        """Run an iterative conversation or plan-based agent.

        Two sub-paths:

        * ``bug_fixing`` — uses :class:`MUTPromptComposer`'s conversation
          manager to exchange multiple turns with the model.
        * ``code_agent`` / ``runtime`` — uses :class:`LangGraphCodeAgent`
          which runs a Planner/Solver state machine and returns the final
          result string.

        After the LangGraph path the global ``litellm.api_base`` is reset
        to prevent cross-contamination of judge calls.
        """
        # Start conversation
        if category == "code_agent":
            # Use LangGraph agent for proper plan-based execution
            from nichebench.frameworks.drupal.prompts.CODE_AGENT import (
                CODE_AGENT_BASE_PROMPT,
            )
            from nichebench.providers.langgraph_code_agent import LangGraphCodeAgent

            # Create the LangGraph agent with correct parameters
            agent = LangGraphCodeAgent(
                model=self.model_str,
                custom_llm_params=self.model_config.get("parameters", {}),
            )

            # Prepare context for the agent
            context = getattr(test_case, "context", None) or test_case.raw.get("context", "")
            task_description = getattr(test_case, "prompt", "") or test_case.raw.get("prompt", "")

            # Create progress callback from runner
            progress_callback = None
            if runner:

                def update_progress(message: str, step: int):
                    runner.update_test_status(f"[yellow]🧪 {test_case.id}[/yellow] - {message}", step)

                progress_callback = update_progress

            # Execute the task - returns string result
            final_output = agent.execute_task(
                task_description=task_description, context=context, progress_callback=progress_callback
            )

            # CRITICAL: Reset global litellm.api_base after LangGraph agent finishes
            # The LangGraph agent uses ChatLiteLLM which sets global state that affects
            # subsequent judge calls. We need to clear this to prevent 404 errors.
            try:
                import litellm

                if hasattr(litellm, "api_base"):
                    setattr(litellm, "api_base", None)
            except ImportError:
                pass  # litellm not available, ignore

            # Build comprehensive input message showing the full prompt chain
            input_parts = [
                f"TASK: {task_description}",
            ]
            if context:
                input_parts.append(f"CONTEXT: {context}")

            input_parts.extend(
                [
                    f"\nSYSTEM PROMPT: {(system_prompt or CODE_AGENT_BASE_PROMPT or '')[:200]}...",
                    "\nEXECUTION: LangGraph plan-based code generation",
                ]
            )

            initial_user_message = "\n".join(input_parts)

            return final_output, initial_user_message

        if category == "bug_fixing":
            conversation = MUTPromptComposer.start_bug_conversation(test_case, system_prompt)
        else:
            raise ValueError(f"Multi-turn not supported for category: {category}")

        # Execute conversation turns
        messages: Optional[List[Dict[str, str]]] = conversation._format_for_llm()
        turn_count = 0

        while messages and turn_count < conversation.max_turns:
            turn_count += 1

            if runner:
                runner.update_test_status(f"[yellow]🧪 {test_case.id}[/yellow] - MUT Turn {turn_count}...", 1)

            try:
                mut_response = self.client.generate_with_messages(
                    messages=messages, model=self.model_str, model_params=self.model_config.get("parameters", {})
                )

                assistant_output = mut_response.get("output", f"[Error: no output from {self.model_str}]")

                # Check for MUT error
                if "[Error:" in assistant_output:
                    return assistant_output, "Multi-turn conversation (see conversation manager for full context)"

                # Continue conversation
                messages = conversation.add_assistant_response(assistant_output)

                # Check for conversation errors
                if hasattr(conversation, "has_error") and conversation.has_error:
                    error_msg = f"[Error: Model misbehavior - {conversation.error_reason}]"
                    return error_msg, "Multi-turn conversation (model error occurred)"

                # Check if complete
                if messages is None:
                    break

            except Exception as e:
                return f"[Error: Exception in turn {turn_count}: {str(e)}]", "Multi-turn conversation (error occurred)"

        # Extract final answer
        final_output = (
            conversation.final_answer
            if conversation.is_complete
            else f"[Error: Conversation incomplete after {turn_count} turns]"
        )

        # Get initial user message for logging
        first_user_message: Optional[str] = None
        for turn in conversation.turns:
            if turn.role == "user":
                first_user_message = turn.content
                break

        return final_output, first_user_message or "Multi-turn conversation"
