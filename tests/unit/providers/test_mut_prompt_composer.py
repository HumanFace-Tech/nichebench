"""Unit tests for MUT prompt composition logic."""

from nichebench.core.datamodel import TestCaseSpec
from nichebench.providers.mut_prompt_composer import MUTPromptComposer


def test_compose_quiz_prompt_with_choices():
    """Test quiz prompt generation with multiple choice options."""
    test_case = TestCaseSpec(
        id="test_001",
        type="quiz",
        prompt="",
        raw={
            "question": "What is the primary purpose of Drupal?",
            "choices": ["Website building", "Data analysis", "Game development"],
            "correct_choice": "A",
        },
    )
    test_case.choices = ["Website building", "Data analysis", "Game development"]

    prompt = MUTPromptComposer.compose_quiz_prompt(test_case)

    assert "Question: What is the primary purpose of Drupal?" in prompt
    assert "A) Website building" in prompt
    assert "B) Data analysis" in prompt
    assert "C) Game development" in prompt
    assert "Your answer (letter only):" in prompt


def test_compose_quiz_prompt_with_system_prompt():
    """Test quiz prompt with system prompt injection."""
    test_case = TestCaseSpec(
        id="test_002", type="quiz", prompt="", raw={"question": "Test question", "choices": ["A", "B"]}
    )
    test_case.choices = ["Option A", "Option B"]

    system_prompt = "You are a Drupal expert. Answer concisely."
    prompt = MUTPromptComposer.compose_quiz_prompt(test_case, system_prompt)

    assert prompt.startswith("You are a Drupal expert. Answer concisely.")
    assert "Question: Test question" in prompt
    assert "Your answer (letter only):" in prompt


def test_compose_quiz_prompt_with_context():
    """Test quiz prompt with additional context."""
    test_case = TestCaseSpec(
        id="test_003",
        type="quiz",
        prompt="",
        context="Drupal is a content management system.",
        raw={"question": "What version introduced composer?", "choices": ["7", "8", "9"]},
    )
    test_case.choices = ["7", "8", "9"]

    prompt = MUTPromptComposer.compose_quiz_prompt(test_case)

    assert "Context: Drupal is a content management system." in prompt
    assert "Question: What version introduced composer?" in prompt


def test_compose_quiz_prompt_without_choices():
    """Test quiz prompt without predefined choices (open-ended)."""
    test_case = TestCaseSpec(
        id="test_004",
        type="quiz",
        prompt="Explain the hook system in Drupal",
        raw={"question": "Explain the hook system in Drupal"},
    )
    # No choices set

    prompt = MUTPromptComposer.compose_quiz_prompt(test_case)

    assert "Question: Explain the hook system in Drupal" in prompt
    assert "Your answer:" in prompt
    assert "Your answer (letter only):" not in prompt


def test_compose_code_prompt_basic():
    """Test code generation prompt composition."""
    test_case = TestCaseSpec(
        id="code_001",
        type="code_generation",
        prompt="Create a Drupal module that displays 'Hello World'",
        raw={"prompt": "Create a Drupal module that displays 'Hello World'"},
    )

    prompt = MUTPromptComposer.compose_code_prompt(test_case)

    assert "Create a Drupal module that displays 'Hello World'" in prompt


def test_compose_code_prompt_with_context_and_system():
    """Test code prompt with context and system prompt."""
    test_case = TestCaseSpec(
        id="code_002",
        type="code_generation",
        prompt="Implement user registration validation",
        context="Drupal 10 project with custom authentication",
        raw={"prompt": "Implement user registration validation"},
    )

    system_prompt = "Generate clean, well-documented Drupal code."
    prompt = MUTPromptComposer.compose_code_prompt(test_case, system_prompt)

    assert prompt.startswith("Generate clean, well-documented Drupal code.")
    assert "Context: Drupal 10 project with custom authentication" in prompt
    assert "Implement user registration validation" in prompt


def test_compose_bug_prompt_basic():
    """Test bug fixing prompt composition."""
    test_case = TestCaseSpec(
        id="bug_001",
        type="bug_fixing",
        prompt="Fix the memory leak in this Drupal module",
        raw={"prompt": "Fix the memory leak in this Drupal module"},
    )

    prompt = MUTPromptComposer.compose_bug_prompt(test_case)

    assert "Fix the memory leak in this Drupal module" in prompt


def test_compose_prompt_generic_fallback():
    """Test generic prompt composition for unknown categories."""
    test_case = TestCaseSpec(
        id="generic_001",
        type="unknown",
        prompt="Generic task",
        context="Some context",
        raw={"question": "What is this?", "prompt": "Generic task"},
    )

    # Use unknown category
    prompt = MUTPromptComposer.compose_prompt(test_case, category="unknown")

    assert "Generic task" in prompt


def test_compose_prompt_category_routing():
    """Test that compose_prompt routes to correct category-specific methods."""
    test_case = TestCaseSpec(
        id="routing_001", type="quiz", prompt="", raw={"question": "Test quiz routing", "choices": ["A", "B"]}
    )
    test_case.choices = ["Option A", "Option B"]

    # Test quiz routing
    quiz_prompt = MUTPromptComposer.compose_prompt(test_case, category="quiz")
    assert "Your answer (letter only):" in quiz_prompt

    # Test code routing
    code_prompt = MUTPromptComposer.compose_prompt(test_case, category="code_generation")
    assert "Your answer (letter only):" not in code_prompt

    # Test bug routing
    bug_prompt = MUTPromptComposer.compose_prompt(test_case, category="bug_fixing")
    assert "Your answer (letter only):" not in bug_prompt
