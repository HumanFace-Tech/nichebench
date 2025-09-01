"""Unit tests for core datamodel classes."""

from nichebench.core.datamodel import TaskSpec, TestCaseSpec


def test_testcase_spec_creation():
    """Test basic TestCaseSpec creation and field access."""
    test_case = TestCaseSpec(
        id="test_001",
        type="quiz",
        raw={"question": "What is Drupal?", "choices": ["CMS", "Framework"]},
        context="Drupal background",
        choices=["CMS", "Framework"],
        correct_choice="A",
    )

    assert test_case.id == "test_001"
    assert test_case.type == "quiz"
    assert test_case.context == "Drupal background"
    assert test_case.choices == ["CMS", "Framework"]
    assert test_case.correct_choice == "A"
    assert test_case.raw["question"] == "What is Drupal?"


def test_testcase_spec_optional_fields():
    """Test TestCaseSpec with minimal required fields."""
    test_case = TestCaseSpec(id="minimal_001", type="code_generation", raw={"prompt": "Write a function"})

    assert test_case.id == "minimal_001"
    assert test_case.type == "code_generation"
    assert test_case.context is None
    assert test_case.choices is None
    assert test_case.checklist is None


def test_testcase_spec_with_checklist():
    """Test TestCaseSpec with evaluation checklist."""
    checklist = ["Function is syntactically correct", "Function handles edge cases", "Code follows Drupal standards"]

    test_case = TestCaseSpec(
        id="code_001", type="code_generation", raw={"prompt": "Create a Drupal hook"}, checklist=checklist
    )

    assert test_case.checklist == checklist
    assert len(test_case.checklist) == 3


def test_task_spec_creation():
    """Test TaskSpec creation with test cases."""
    test_cases = [
        TestCaseSpec(id="tc1", type="quiz", raw={"q": "Q1"}),
        TestCaseSpec(id="tc2", type="quiz", raw={"q": "Q2"}),
    ]

    task = TaskSpec(framework="drupal", task_type="quiz", file_path="/path/to/quiz.yaml", testcases=test_cases)

    assert task.framework == "drupal"
    assert task.task_type == "quiz"
    assert task.file_path == "/path/to/quiz.yaml"
    assert len(task.testcases) == 2
    assert task.testcases[0].id == "tc1"


def test_task_spec_empty_testcases():
    """Test TaskSpec with default empty testcases list."""
    task = TaskSpec(framework="drupal", task_type="code_generation", file_path="/path/to/code.yaml")

    assert task.testcases == []
    assert len(task.testcases) == 0


def test_testcase_spec_raw_dict_access():
    """Test accessing nested data from raw dict."""
    raw_data = {
        "question": "How do you create a custom block in Drupal?",
        "choices": ["Plugin", "Hook", "Service"],
        "metadata": {"difficulty": "intermediate", "tags": ["blocks", "plugins"]},
    }

    test_case = TestCaseSpec(id="advanced_001", type="quiz", raw=raw_data)

    assert test_case.raw["question"] == "How do you create a custom block in Drupal?"
    assert test_case.raw["metadata"]["difficulty"] == "intermediate"
    assert "plugins" in test_case.raw["metadata"]["tags"]
