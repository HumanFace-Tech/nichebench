"""Unit tests for LangGraph code generation agent."""

from unittest.mock import Mock, patch

import pytest

from nichebench.providers.langgraph_code_agent import (
    LangGraphCodeAgent,
    should_continue,
)


class TestLangGraphCodeAgent:
    """Unit tests for LangGraphCodeAgent class."""

    @patch("nichebench.providers.langgraph_code_agent.ChatLiteLLM")
    def test_init_with_default_params(self, mock_litellm):
        """Test agent initialization with default parameters."""
        mock_llm = Mock()
        mock_litellm.return_value = mock_llm

        agent = LangGraphCodeAgent()

        assert agent.model == "groq/llama-3.1-8b-instant"
        assert agent.custom_llm_params == {}
        assert agent.llm == mock_llm


class TestGraphRouting:
    """Unit tests for graph routing logic."""

    def test_should_continue_when_complete(self):
        """Test routing when task is complete."""
        state = {
            "is_complete": True,
        }
        assert should_continue(state) == "__end__"

    def test_should_continue_to_solver(self):
        """Test routing to solver for next step."""
        state = {
            "plan": ["step1", "step2"],
            "current_step_index": 0,
            "is_complete": False,
        }
        assert should_continue(state) == "solver"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
