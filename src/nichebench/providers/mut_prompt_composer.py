"""Prompt composer for Model Under Test (MUT) - formats test cases into proper prompts and manages conversations."""

from __future__ import annotations

from typing import Optional

from nichebench.core.datamodel import TestCaseSpec
from nichebench.providers.conversation_manager import ConversationManager


class MUTPromptComposer:
    """Composes prompts for the model-under-test based on test case type."""

    @staticmethod
    def compose_quiz_prompt(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> str:
        """Compose a proper multiple choice quiz prompt for the MUT.

        Returns a well-formatted prompt with:
        - System prompt (if provided)
        - Context (if available)
        - Question
        - Multiple choice options
        - Clear instruction for response format
        """
        parts = []

        # Add system prompt
        if system_prompt:
            parts.append(system_prompt.strip())

        # Add context if available
        if test_case.context:
            parts.append(f"Context: {test_case.context}")

        # Add the question
        question = test_case.raw.get("question") or test_case.prompt or ""
        parts.append(f"Question: {question}")

        # Add the choices
        if test_case.choices:
            choices_text = "Choices:"
            for i, choice in enumerate(test_case.choices):
                letter = chr(65 + i)  # A, B, C, D, ...
                choices_text += f"\n{letter}) {choice}"
            parts.append(choices_text)
            parts.append("Your answer (letter only):")
        else:
            parts.append("Your answer:")

        return "\n\n".join(parts)

    @staticmethod
    def compose_code_prompt(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> str:
        """Compose a code generation prompt for the MUT."""
        parts = []

        if system_prompt:
            parts.append(system_prompt.strip())

        if test_case.context:
            parts.append(f"Context: {test_case.context}")

        prompt_text = test_case.prompt or test_case.raw.get("prompt") or ""
        parts.append(prompt_text)

        return "\n\n".join(parts)

    @staticmethod
    def compose_bug_prompt(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> str:
        """Compose a bug fixing prompt for the MUT."""
        parts = []

        if system_prompt:
            parts.append(system_prompt.strip())

        if test_case.context:
            parts.append(f"Context: {test_case.context}")

        prompt_text = test_case.prompt or test_case.raw.get("prompt") or ""
        parts.append(prompt_text)

        return "\n\n".join(parts)

    @staticmethod
    def compose_prompt(test_case: TestCaseSpec, system_prompt: Optional[str] = None, category: str = "quiz") -> str:
        """Compose a prompt based on the category type."""
        if category == "quiz":
            return MUTPromptComposer.compose_quiz_prompt(test_case, system_prompt)
        elif category == "code_generation":
            return MUTPromptComposer.compose_code_prompt(test_case, system_prompt)
        elif category == "bug_fixing":
            return MUTPromptComposer.compose_bug_prompt(test_case, system_prompt)
        else:
            # Generic fallback
            parts = []
            if system_prompt:
                parts.append(system_prompt.strip())

            prompt_text = (
                test_case.prompt
                or test_case.raw.get("question")
                or test_case.raw.get("prompt")
                or test_case.context
                or ""
            )
            parts.append(prompt_text)

            return "\n\n".join(parts)

    # Conversation management methods (for multi-turn tasks)

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
