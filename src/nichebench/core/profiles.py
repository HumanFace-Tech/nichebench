from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ToolProfile:
    """Represents a set of capabilities allowed for the agent (Task 5.1)."""

    name: str
    allow_web_search: bool = False
    allow_browser: bool = False
    allow_mcp: bool = False
    allow_external_network_for_shell: bool = False


# Task 5.1 Profile Presets
PROFILES = {
    "offline_cli": ToolProfile(
        name="offline_cli",
        allow_web_search=False,
        allow_browser=False,
        allow_mcp=True,
        allow_external_network_for_shell=False,
    ),
    "web_cli": ToolProfile(
        name="web_cli",
        allow_web_search=True,
        allow_browser=False,
        allow_mcp=True,
        allow_external_network_for_shell=True,
    ),
    "web_plus_browser": ToolProfile(
        name="web_plus_browser",
        allow_web_search=True,
        allow_browser=True,
        allow_mcp=True,
        allow_external_network_for_shell=True,
    ),
}


def resolve_profile(profile_name: str, overrides: Optional[Dict[str, Any]] = None) -> ToolProfile:
    """Resolve a profile name to its effective flags (Task 5.1)."""
    base = PROFILES.get(profile_name, PROFILES["offline_cli"])

    if not overrides:
        return base

    return ToolProfile(
        name=f"custom-{profile_name}",
        allow_web_search=overrides.get("allow_web_search", base.allow_web_search),
        allow_browser=overrides.get("allow_browser", base.allow_browser),
        allow_mcp=overrides.get("allow_mcp", base.allow_mcp),
        allow_external_network_for_shell=overrides.get(
            "allow_external_network_for_shell", base.allow_external_network_for_shell
        ),
    )


def enforce_profile_restrictions(agent_config: Dict[str, Any], profile: ToolProfile):
    """Enforce profile-specific tool restrictions (Task 5.2)."""
    # This would typically modify the agent's tool config
    if "tools" not in agent_config:
        agent_config["tools"] = {}

    tools = agent_config["tools"]

    # Disable/Enable tools based on profile
    if not profile.allow_web_search:
        tools["web_search"] = {"enabled": False}

    if not profile.allow_browser:
        tools["browser"] = {"enabled": False}

    if not profile.allow_external_network_for_shell:
        tools["shell_network"] = {"enabled": False}

    # Mark the effective profile (Task 5.3)
    agent_config["effective_profile"] = profile.name
    agent_config["resolved_flags"] = {
        "allow_web_search": profile.allow_web_search,
        "allow_browser": profile.allow_browser,
        "allow_mcp": profile.allow_mcp,
        "allow_external_network_for_shell": profile.allow_external_network_for_shell,
    }
