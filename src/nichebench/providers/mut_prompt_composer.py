"""Prompt composer for Model Under Test (MUT).

Formats test cases into structured prompts and manages multi-turn conversations.

## Ownership and Responsibilities

This module is a **pure composer**: it reads `TestCaseSpec` objects and produces formatted strings
or `ConversationManager` instances. It does **not** own task definitions, scoring logic, or
evaluation state. All inputs are received from callers; all outputs are returned to callers.

## Caller Expectations

Callers (e.g. the executor) are responsible for:
- Constructing a valid `TestCaseSpec` with the appropriate fields populated for the target category.
- Passing the correct `category` to `compose_prompt()`; no validation is performed here.
- Providing an optional `system_prompt` when the task requires one (not injected by this module).

## Prompt-Composition Constraints

- **No side effects.** Composing a prompt does not modify the `TestCaseSpec` or any global state.
- **Deterministic output.** Same inputs → same output, regardless of call count.
- **Null safety.** Missing optional fields (e.g. `context`, `choices`) are handled gracefully with
  empty-string fallbacks; no exceptions are raised for absent data.
- **Category-specific contracts:**
  - `compose_quiz_prompt` — enforces a letter-only answer constraint and renders
    each choice exactly once with a dynamic letter (`A`, `B`, …).
  - `compose_code_prompt` / `start_code_conversation` — passes raw prompt text verbatim after context.
  - `compose_bug_prompt` / `start_bug_conversation` — passes raw prompt text verbatim after context.
  - Unknown categories fall through to a generic fallback that concatenates the first available
    text field (`prompt` → `question` → `context`).
"""

from __future__ import annotations

from typing import Optional

from nichebench.core.datamodel import TestCaseSpec
from nichebench.core.quiz_text import normalize_choice
from nichebench.providers.conversation_manager import ConversationManager


class MUTPromptComposer:
    """Composes prompts for the model-under-test based on test case type.

    Two composition styles are offered:

    1. **One-shot prompts** — `compose_*_prompt()` methods return a single formatted string
       suitable for a single LLM call (or the first turn of a conversation).
    2. **Conversation managers** — `start_*_conversation()` methods return an initialised
       `ConversationManager` that holds the conversation state across multiple turns.

    All methods are static; no instance state is maintained. The class is a namespace for
    category-specific composition logic only.
    """

    @staticmethod
    def compose_quiz_prompt(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> str:
        """Compose a multiple-choice quiz prompt for the MUT.

        **Output contract** — sections are joined by two blank lines in this order:

        1. ``system_prompt`` (if provided), stripped of leading/trailing whitespace.
        2. ``Context: <test_case.context>`` (if ``test_case.context`` is set).
        3. ``Question: <question>`` — drawn from  ``test_case.raw["question"]``,
           falling back to ``test_case.prompt``.
        4. ``Choices:`` block — one line per choice, each prefixed exactly once
           with a dynamic letter (``A``, ``B``, … ``chr(64 + n)``).  A redundant
           letter prefix already present in the choice text (``A) text``,
           ``A) A) text``, ``(A) text``) is stripped first, so the choice is
           never rendered twice.  If no choices are present this block is omitted.
        5. ``Your answer (letter only):`` — enforces letter-only response format.

        **Caller responsibility** — ``test_case.choices`` should be a list of
        strings when the question is multiple-choice.  Values are normalised
        defensively, so a leftover letter prefix never reaches the prompt.
        """
        parts = []

        # Add system prompt
        if system_prompt and system_prompt.strip():
            parts.append(system_prompt.strip())

        # Add context if available
        context = (test_case.context or "").strip()
        if context:
            parts.append(f"Context: {context}")

        # Add the question
        question = test_case.raw.get("question") or test_case.prompt or ""
        parts.append(f"Question: {question}")

        # Add the choices — each choice is rendered exactly once
        choices = [normalize_choice(choice) for choice in (test_case.choices or [])]
        if choices:
            choices_text = "Choices:"
            for i, choice in enumerate(choices):
                letter = chr(65 + i)  # A, B, C, D, ...
                choices_text += f"\n{letter}) {choice}"
            parts.append(choices_text)
            parts.append("Your answer (letter only):")
        else:
            parts.append("Your answer:")

        return "\n\n".join(parts)

    @staticmethod
    def compose_code_prompt(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> str:
        """Compose a code-generation prompt for the MUT.

        **Output contract** — sections are joined by two blank lines in this order:

        1. ``system_prompt`` (if provided), stripped.
        2. ``Context: <test_case.context>`` (if set).
        3. Raw prompt text — taken from ``test_case.prompt``, falling back to
           ``test_case.raw["prompt"]``. The text is inserted verbatim; no additional
           formatting or instruction wrapping is applied.

        **Intended use** — caller is expected to include any explicit instruction
        (e.g. "Write a function that ...") inside ``test_case.prompt`` before calling
        this method.
        """
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
        """Compose a bug-fixing prompt for the MUT.

        **Output contract** — sections are joined by two blank lines in this order:

        1. ``system_prompt`` (if provided), stripped.
        2. ``Context: <test_case.context>`` (if set).
        3. Raw prompt text — taken from ``test_case.prompt``, falling back to
           ``test_case.raw["prompt"]``. The text is inserted verbatim; no additional
           framing or instruction wrapping is applied.

        **Intended use** — the prompt text should describe the bug and expected fix;
        caller is responsible for ensuring the description is self-contained.
        """
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
        """Compose a prompt based on the category type.

        Args:
            test_case: The test case specification. Must have fields populated
                appropriate to the category (see individual compose method docs).
            system_prompt: Optional system-level instruction string.
            category: One of ``"quiz"``, ``"code_generation"``, ``"bug_fixing"``.
                Defaults to ``"quiz"``. Unknown values fall through to a generic
                fallback that concatenates the first available text field.

        Returns:
            A formatted prompt string; format depends on ``category``.
            See the specific ``compose_<category>_prompt`` methods for the exact
            output contract of each supported category.
        """
        if category == "quiz":
            return MUTPromptComposer.compose_quiz_prompt(test_case, system_prompt)
        if category == "code_generation":
            return MUTPromptComposer.compose_code_prompt(test_case, system_prompt)
        if category == "bug_fixing":
            return MUTPromptComposer.compose_bug_prompt(test_case, system_prompt)
        # Generic fallback
        parts = []
        if system_prompt:
            parts.append(system_prompt.strip())

        prompt_text = (
            test_case.prompt or test_case.raw.get("question") or test_case.raw.get("prompt") or test_case.context or ""
        )
        parts.append(prompt_text)

        return "\n\n".join(parts)

    # Conversation management methods (for multi-turn tasks)

    @staticmethod
    def start_code_conversation(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> ConversationManager:
        """Start a new multi-turn conversation for code generation.

        **Conversation contract**

        - ``ConversationManager`` is initialised with ``max_turns=5``.
        - The initial user prompt is composed as::

              Context: <test_case.context>   (if set)
              <test_case.prompt or test_case.raw["prompt"]>

          No multi-turn framing instructions are injected; the caller is responsible
          for any turn-management prompts.
        - ``system_prompt`` (or an empty string) is passed to
          ``conversation.start_conversation()`` as the system-level instruction.

        Returns:
            A ``ConversationManager`` with the initial turn already recorded.
        """
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
    def start_bug_conversation(test_case: TestCaseSpec, system_prompt: Optional[str] = None) -> ConversationManager:
        """Start a multi-turn conversation for bug fixing.

        **Conversation contract**

        - ``ConversationManager`` is initialised with ``max_turns`` read from
          ``test_case.raw["max_turns"]`` (manifest ``max_turns``).  The value
          defaults to ``4`` when absent or unparseable and is clamped to
          ``1..8``.
        - The initial user prompt is composed as::

              Context: <test_case.context>   (if set)
              <test_case.prompt or test_case.raw["prompt"]>

          No multi-turn framing instructions are injected; the caller is responsible
          for any turn-management prompts.
        - ``system_prompt`` (or an empty string) is passed to
          ``conversation.start_conversation()``.

        Returns:
            A ``ConversationManager`` with the initial turn already recorded.
        """
        raw_max_turns = test_case.raw.get("max_turns", 4)
        try:
            max_turns = int(raw_max_turns)
        except (TypeError, ValueError):
            max_turns = 4
        max_turns = max(1, min(8, max_turns))
        conversation = ConversationManager(max_turns=max_turns)

        # Compose the initial user prompt - NO multi-turn instructions here
        parts = []

        if test_case.context:
            parts.append(f"Context: {test_case.context}")

        prompt_text = test_case.prompt or test_case.raw.get("prompt") or ""
        parts.append(prompt_text)

        user_prompt = "\n\n".join(parts)

        conversation.start_conversation(system_prompt=system_prompt or "", user_prompt=user_prompt)

        return conversation
