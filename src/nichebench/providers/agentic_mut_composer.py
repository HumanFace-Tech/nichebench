"""Multi-turn prompt composer for agentic model interactions."""

from typing import Any, Dict, List, Optional

from nichebench.core.datamodel import TestCaseSpec
from nichebench.providers.conversation_manager import ConversationManager


class AgenticMUTPromptComposer:
    """Composes prompts for multi-turn agentic conversations with the model-under-test."""

    @staticmethod
    def start_code_conversation(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> ConversationManager:
        """Start a new multi-turn conversation for code generation."""
        conversation = ConversationManager(max_turns=5)

        # Compose the initial user prompt - NO multi-turn instructions here
        parts = []

        if test_case.context:
            parts.append(f"Context: {test_case.context}")

        prompt_text = test_case.prompt or test_case.raw.get("prompt") or ""
        parts.append(prompt_text)

        user_prompt = "\n\n".join(parts)

        # Start the conversation
        conversation.start_conversation(system_prompt=system_prompt or "", user_prompt=user_prompt)

        return conversation

    @staticmethod
    def start_quiz_conversation(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> ConversationManager:
        """Start a conversation for quiz questions (typically single-turn)."""
        conversation = ConversationManager(max_turns=1)  # Quizzes should be single-turn

        parts = []

        if system_prompt:
            # System prompt will be added by conversation manager
            pass

        if test_case.context:
            parts.append(f"Context: {test_case.context}")

        question = test_case.raw.get("question") or test_case.prompt or ""
        parts.append(f"Question: {question}")

        if test_case.choices:
            choices_text = "Choices:"
            for i, choice in enumerate(test_case.choices):
                letter = chr(65 + i)  # A, B, C, D, ...
                choices_text += f"\n{letter}) {choice}"
            parts.append(choices_text)
            parts.append("Your answer (letter only):")
        else:
            parts.append("Your answer:")

        user_prompt = "\n\n".join(parts)

        conversation.start_conversation(system_prompt=system_prompt or "", user_prompt=user_prompt)

        return conversation

    @staticmethod
    def start_bug_conversation(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> ConversationManager:
        """Start a multi-turn conversation for bug fixing."""
        conversation = ConversationManager(max_turns=4)  # Bug fixes might need a few turns

        # Compose the initial user prompt - NO multi-turn instructions here
        parts = []

        if test_case.context:
            parts.append(f"Context: {test_case.context}")

        prompt_text = test_case.prompt or test_case.raw.get("prompt") or ""
        parts.append(prompt_text)

        user_prompt = "\n\n".join(parts)

        conversation.start_conversation(system_prompt=system_prompt or "", user_prompt=user_prompt)

        return conversation
