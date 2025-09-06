"""Multi-turn conversation manager for agentic code generation."""

from typing import Any, Dict, List, Optional


class ConversationTurn:
    """Represents a single turn in the conversation."""

    def __init__(self, role: str, content: str):
        self.role = role  # "system", "user", "assistant"
        self.content = content


class ConversationManager:
    """Manages multi-turn conversations with agentic models for iterative code generation."""

    def __init__(self, max_turns: int = 5):
        self.max_turns = max_turns
        self.turns: List[ConversationTurn] = []
        self.final_answer = ""
        self.is_complete = False
        self.has_error = False
        self.error_reason: Optional[str] = None

    def start_conversation(self, system_prompt: str, user_prompt: str) -> List[Dict[str, str]]:
        """Start a new conversation with system and user prompts."""
        self.turns = []
        self.final_answer = ""
        self.is_complete = False

        # Add system prompt
        self.turns.append(ConversationTurn("system", system_prompt))

        # Add user prompt
        self.turns.append(ConversationTurn("user", user_prompt))

        return self._format_for_llm()

    def add_assistant_response(self, response: str) -> Optional[List[Dict[str, str]]]:
        """
        Add assistant response and determine if conversation should continue.

        Returns:
            - None if conversation is complete (assistant said they're done)
            - List of messages if conversation should continue
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

        if max_repetitions > 3:
            return True

        return False

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
        """Get a summary of the conversation for debugging."""
        return {
            "total_turns": len(self.turns),
            "assistant_turns": len([t for t in self.turns if t.role == "assistant"]),
            "is_complete": self.is_complete,
            "final_answer_length": len(self.final_answer) if self.final_answer else 0,
            "max_turns_reached": len([t for t in self.turns if t.role == "assistant"]) >= self.max_turns,
        }
