"""Agent prompt composition, identity, and product branding."""

from .agent_identity import (
    get_agent_display_name,
    get_agent_label,
    is_judge_like_agent,
    try_resolve_canonical_id,
)
from .agent_setup import (
    NativeAgentFields,
    ValidationReport,
    graph_custom_agents,
    load_mcp_needs_map,
    parse_agent_file,
    system_prompts_by_key,
    validate_agents,
)
from .branding import APP_DOT_SLUG, APP_HOME_DIRNAME, APP_NAME

__all__ = [
    "APP_DOT_SLUG",
    "APP_HOME_DIRNAME",
    "APP_NAME",
    "NativeAgentFields",
    "ValidationReport",
    "get_agent_display_name",
    "get_agent_label",
    "graph_custom_agents",
    "is_judge_like_agent",
    "load_mcp_needs_map",
    "parse_agent_file",
    "system_prompts_by_key",
    "try_resolve_canonical_id",
    "validate_agents",
]
