from pathlib import Path

from nichebench.core.prompt_loader import load_prompt_text

_PROMPTS_PATH = Path(__file__).with_name("code_agent.yaml")

CODE_AGENT_BASE_PROMPT = load_prompt_text(_PROMPTS_PATH, "base_prompt", default="")
CODE_AGENT_PLANNER_PROMPT = load_prompt_text(_PROMPTS_PATH, "planner_prompt", default="")
CODE_AGENT_SOLVER_PROMPT = load_prompt_text(_PROMPTS_PATH, "solver_prompt", default="")
CODE_AGENT_PLANNER_REQUEST_TEMPLATE = load_prompt_text(_PROMPTS_PATH, "planner_request_template", default="")
CODE_AGENT_SOLVER_REQUEST_TEMPLATE = load_prompt_text(_PROMPTS_PATH, "solver_request_template", default="")

# Backward compatibility: some runners may use this as the system prompt.
CODE_AGENT_SYSTEM_PROMPT = CODE_AGENT_BASE_PROMPT
