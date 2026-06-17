"""Multi-turn conversation manager for agentic code generation.

This module provides stateful multi-turn conversation management for iterative
code generation workflows. It handles the conversation protocol between the
caller (typically a LangGraph agent) and a remote LLM via LiteLLM.

Ownership Model
===============
This manager is a **stateless-from-the-caller perspective** helper that owns only
its own internal state (`turns`, `is_complete`, `final_answer`, `error_reason`).
It does **not** own the LiteLLM client, the model-under-test, or any network
handle. The caller is responsible for:

- Creating and configuring the LiteLLM client.
- Making actual API calls and handling retries/timeouts.
- Resetting `litellm.api_base` after each agent run (the LangGraph executor resets
  this automatically; do not break that contract).
- Destroying the manager instance when the conversation is done.

Thread Safety
=============
`ConversationManager` is **not thread-safe**. Each concurrent conversation must
use its own instance. Sharing a single instance across threads will corrupt
`turns`, `is_complete`, and `final_answer`.

Multi-Turn Conversation Contract
=================================
The manager enforces a **finalization loop** on top of the raw model responses:

1. Caller starts a conversation with ``start_conversation()``.
2. Caller sends the initial message list to the LLM and receives a response.
3. Caller calls ``add_assistant_response(response)`` with the raw LLM output.
4. ``add_assistant_response`` either:
   - Returns a *continuation message list* (injected finalization prompt) so the
     caller can ask the model to finalize, OR
   - Returns ``None`` indicating the conversation is complete (terminal state).
5. If continuation: caller sends the returned messages to the LLM, gets a response,
   and calls ``add_assistant_response`` again.
6. Loop until ``None`` is returned or an error is set.

Error Detection
===============
The manager detects two error conditions internally:

- **Repetitive response**: Model generates >3 repetitions of a 100-char chunk
  in responses ≥1000 chars. When detected, ``has_error`` is set to ``True``,
  ``error_reason`` is ``"repetitive_response"``, and ``is_complete`` is ``True``.
  The ``final_answer`` is set to a sentinel error string.

- **Max turns reached**: When the number of assistant turns equals ``max_turns``,
  ``is_complete`` is set to ``True`` and ``final_answer`` is extracted from all
  prior assistant turns.

In both cases ``add_assistant_response`` returns ``None``, signalling the caller
to stop sending messages.

Caller Expectations
==================
The caller MUST respect the terminal state set by this manager:

- When ``add_assistant_response`` returns ``None``, do not send further messages.
- When ``has_error`` is ``True``, treat the run as failed; do not retry within
  the same ``ConversationManager`` instance.
- ``final_answer`` is only populated after ``is_complete`` becomes ``True``.
  Reading it earlier yields an empty string.
"""

from typing import Any, Dict, List, Optional


class ConversationTurn:
    """Represents a single turn in a multi-turn conversation.

    A ``ConversationTurn`` is a plain data holder: it records the ``role`` and
    ``content`` of one message in the turn history. It has no validation logic.

    Args:
        role: One of ``"system"``, ``"user"``, or ``"assistant"``. Caller code
            is responsible for ensuring only these three values are passed.
        content: The text content of the turn. May be an empty string.

    Attributes:
        role: The role that sent this turn.
        content: The text content of the turn.
    """

    def __init__(self, role: str, content: str):
        self.role = role  # "system", "user", or "assistant"
        self.content = content


class ConversationManager:
    """Manages multi-turn conversations with agentic models for iterative code generation.

    The manager maintains the full turn history internally, drives a finalization
    loop to coax complete answers from the model, and detects error conditions
    (repetitive output, max-turns exhaustion).

    State Variables
    ---------------
    ``turns``: List[ConversationTurn]
        All turns in chronological order. Starts empty; populated by
        ``start_conversation`` and ``add_assistant_response``.
    ``is_complete``: bool
        ``True`` when the conversation has reached a terminal state (FINAL received,
        max turns reached, or error detected). Caller should stop sending messages.
    ``has_error``: bool
        ``True`` when an error condition was detected. The run should be treated
        as failed regardless of ``final_answer`` content.
    ``error_reason``: Optional[str]
        Human-readable reason for the error, or ``None`` if no error occurred.
        Current values: ``"repetitive_response"``.
    ``final_answer``: str
        Concatenated content of all assistant turns (excluding sentinel ``FINAL``
        responses), joined by ``"\\n\\n---\\n\\n"``. Only non-empty when
        ``is_complete`` is ``True``.

    Args:
        max_turns: Maximum number of assistant turns before forcing completion.
            Defaults to 5. Must be at least 1.
    """

    def __init__(self, max_turns: int = 5):
        self.max_turns = max_turns
        self.turns: List[ConversationTurn] = []
        self.final_answer = ""
        self.is_complete = False
        self.has_error = False
        self.error_reason: Optional[str] = None

    def start_conversation(self, system_prompt: str, user_prompt: str) -> List[Dict[str, str]]:
        """Start a new conversation and return the initial message list for LiteLLM.

        This method resets all internal state before beginning. Subsequent calls
        to ``start_conversation`` on the same instance are treated as a fresh run
        (previous turn history is discarded).

        Args:
            system_prompt: The system-level instruction prompt (e.g., task description,
                style guidelines). Must be a non-empty string.
            user_prompt: The initial user message describing the task. Must be a
                non-empty string.

        Returns:
            A list of LiteLLM-formatted message dicts with keys ``"role"`` and
            ``"content"``, suitable for passing directly to ``litellm.acompletion``.
            The list always starts with the system prompt followed by the user prompt.

        Raises:
            TypeError: If ``system_prompt`` or ``user_prompt`` is not a string.
        """
        self.turns = []
        self.final_answer = ""
        self.is_complete = False
        self.has_error = False
        self.error_reason = None

        # Add system prompt
        self.turns.append(ConversationTurn("system", system_prompt))

        # Add user prompt
        self.turns.append(ConversationTurn("user", user_prompt))

        return self._format_for_llm()

    def add_assistant_response(self, response: str) -> Optional[List[Dict[str, str]]]:
        """Record an assistant response and drive the finalization loop.

        Call this after each LiteLLM response. The method:

        1. Detects **repetitive content** (model runaway). If found, sets error state
           and returns ``None``.
        2. Detects a bare ``FINAL`` token (model signals done). If found, extracts
           the final answer, sets ``is_complete=True``, and returns ``None``.
        3. Detects **max-turns exhaustion**. If the configured ``max_turns`` has been
           reached, extracts the final answer, sets ``is_complete=True``, and
           returns ``None``.
        4. Otherwise, injects a finalization prompt as the next user turn and
           returns the updated message list for the caller to send to the LLM.

        Args:
            response: The raw string content returned by the LLM for the most
                recent assistant turn. Must be a string.

        Returns:
            - ``None`` if the conversation is complete (terminal state reached).
              Caller MUST NOT send further messages.
            - A list of LiteLLM message dicts if the conversation should continue.
              Caller SHOULD send this list to the LLM and call this method again
              with the model's next response.

        Side Effects:
            - Appends a ``ConversationTurn("assistant", response)`` to ``turns``.
            - May append a ``ConversationTurn("user", <finalization prompt>)`` to
              ``turns`` (when returning a non-``None`` list).
            - Sets ``is_complete=True`` and populates ``final_answer`` on terminal
              conditions.
            - Sets ``has_error=True`` and ``error_reason`` on repetitive detection.
        """
        # Check for repetitive responses - bail out immediately
        if self._is_repetitive_response(response):
            print(f"ERROR: Detected repetitive/runaway response ({len(response)} chars)")
            print("Model is misbehaving with repetitive content - bailing out")
            self.has_error = True
            self.error_reason = "repetitive_response"
            self.is_complete = True
            self.final_answer = "[ERROR: Model generated repetitive content - evaluation skipped]"
            return None

        self.turns.append(ConversationTurn("assistant", response))

        # Check if this is a "FINAL" response (just the word "FINAL" and nothing else significant)
        if self._is_final_response(response):
            self.is_complete = True
            self.final_answer = self._extract_final_answer()
            return None

        # Check if we've hit max turns
        if len([t for t in self.turns if t.role == "assistant"]) >= self.max_turns:
            self.is_complete = True
            self.final_answer = self._extract_final_answer()
            return None

        # ALWAYS ask for finalization - regardless of what the model said
        follow_up = self._create_finalization_prompt()
        self.turns.append(ConversationTurn("user", follow_up))

        return self._format_for_llm()

    def _is_final_response(self, response: str) -> bool:
        """Check if the response is just 'FINAL' (indicating completion)."""
        # Strip whitespace and check if response is just "FINAL"
        cleaned = response.strip()
        return cleaned.upper() == "FINAL"

    def _is_repetitive_response(self, response: str) -> bool:
        """Check if the response contains excessive repetition using simple substring detection."""
        if len(response) < 1000:  # Only check longer responses
            return False

        # Split into chunks and look for repeated chunks
        chunk_size = 100  # Look for 100-character repeated chunks
        chunks = []

        for i in range(0, len(response) - chunk_size, chunk_size // 2):  # Overlapping chunks
            chunk = response[i : i + chunk_size].strip()
            if len(chunk) >= chunk_size - 10:  # Only consider near-full chunks
                chunks.append(chunk)

        # Count how many times each chunk appears
        chunk_counts: Dict[str, int] = {}
        for chunk in chunks:
            chunk_counts[chunk] = chunk_counts.get(chunk, 0) + 1

        # If any chunk appears more than 3 times, it's likely repetitive
        max_repetitions = max(chunk_counts.values()) if chunk_counts else 0

        return max_repetitions > 3

    def _create_finalization_prompt(self) -> str:
        """Create a standardized finalization prompt."""
        return """Did you include everything into your previous reply?

If YES: Just reply "FINAL"
If NOT: Add your final touches and alterations (do not repeat yourself - only NEW content)

IMPORTANT: Do not repeat any code you already provided. Only add what's missing."""

    def _extract_final_answer(self) -> str:
        """Extract the complete implementation from all assistant turns (excluding FINAL responses)."""
        assistant_responses = []

        for turn in self.turns:
            if turn.role == "assistant":
                # Skip responses that are just "FINAL"
                if turn.content.strip().upper() == "FINAL":
                    continue
                assistant_responses.append(turn.content)

        return "\n\n---\n\n".join(assistant_responses)

    def _format_for_llm(self) -> List[Dict[str, str]]:
        """Format conversation turns for LiteLLM messages format."""
        messages = []
        for turn in self.turns:
            messages.append({"role": turn.role, "content": turn.content})
        return messages

    def get_conversation_summary(self) -> Dict[str, Any]:
        """Return a summary of the current conversation state for debugging.

        This method is safe to call at any point in the conversation lifecycle,
        including before ``start_conversation`` is called (all values will be zero
        or empty in that case).

        Returns:
            A dict with the following keys:

            ``total_turns``: int
                Total number of turns recorded (all roles).
            ``assistant_turns``: int
                Number of assistant turns recorded.
            ``is_complete``: bool
                Current terminal-state flag.
            ``final_answer_length``: int
                Length of ``final_answer`` in characters (0 if not yet complete).
            ``max_turns_reached``: bool
                ``True`` if the assistant-turn count has reached or exceeded
                ``max_turns``.
        """
        return {
            "total_turns": len(self.turns),
            "assistant_turns": len([t for t in self.turns if t.role == "assistant"]),
            "is_complete": self.is_complete,
            "final_answer_length": len(self.final_answer) if self.final_answer else 0,
            "max_turns_reached": len([t for t in self.turns if t.role == "assistant"]) >= self.max_turns,
        }
