"""Translate graph-owned agent configuration into SDK session kwargs.

The agent is passed **programmatically**: rather than installing a native
``.agent.md`` for the SDK to discover on disk, the runner hands in the agent's
composed prompt + description (from the graph SSOT) and this builds a
``custom_agents=[CustomAgentConfig]`` list, selecting it by ``agent`` name. So the
SDK backend needs no on-disk agents dir.

Graph-owned tools, prompt, description and MCP servers ride on the programmatic
custom agent. ``model`` and ``cwd`` are **session** properties. The custom-agent
``model:`` field is deliberately unused: it only steers a *sub*-agent, and the
runtime silently falls back to the parent model rather than refusing an id it
cannot honour (``copilot/session.py``). Roundtable runs each agent as the session's
primary agent, so pinning the model there is the only way it binds — and it makes an
unavailable id fail loud at ``session.create`` instead of quietly rerouting the run.

Hermetic isolation is **explicit**, not a default. Under ``mode='copilot-cli'`` the
SDK would otherwise discover workspace MCP/skill config and always load the ambient
custom-instruction files (``.github/copilot-instructions.md``, ``AGENTS.md``); the
safe ``skip_custom_instructions`` / ``custom_agents_local_only`` defaults apply only
in ``empty`` mode (``copilot/_mode.py``). So ``build_session_kwargs`` forces
``enable_config_discovery=False``, ``skip_custom_instructions=True``,
``enable_skills=False`` and ``custom_agents_local_only=True`` — the review sees only
the ``mcp_servers`` + prompt we inject. Per-review filesystem state (session store,
``COPILOT_HOME``) is isolated by the ephemeral ``base_directory`` the bridge owns.
The approve-all permission handler resolves prompts for tools the SDK exposes; it
does not grant tools or widen the custom-agent allowlist.
"""

from __future__ import annotations

from typing import Any

from ..result import SUBMISSION_TOOL_NAME, ExecutionPolicy
from .tool_policy import tool_deadline_hooks


def effective_agent_tools(
    tools: tuple[str, ...] | None, *, submission_enabled: bool
) -> tuple[str, ...] | None:
    """Add the reserved engine transport without changing an unrestricted grant."""

    if not submission_enabled or tools is None or SUBMISSION_TOOL_NAME in tools:
        return tools
    return (*tools, SUBMISSION_TOOL_NAME)


def build_custom_agent(
    *,
    name: str,
    prompt: str,
    description: str | None = None,
    display_name: str | None = None,
    tools: tuple[str, ...] | None = None,
    mcp_servers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one SDK ``CustomAgentConfig`` from graph-owned agent fields."""
    cfg: dict[str, Any] = {"name": name, "prompt": prompt, "infer": False}
    if display_name:
        cfg["display_name"] = display_name
    if description:
        cfg["description"] = description
    if tools is not None:
        cfg["tools"] = list(tools)
    if mcp_servers:
        cfg["mcp_servers"] = mcp_servers
    return cfg


def build_session_kwargs(
    *,
    agent: str,
    prompt: str,
    description: str | None = None,
    display_name: str | None = None,
    tools: tuple[str, ...] | None = None,
    model: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    cwd: str | None = None,
    execution_policy: ExecutionPolicy | None = None,
    submission_enabled: bool = False,
) -> dict[str, Any]:
    """Build the kwargs passed to ``create_session``/``resume_session``.

    ``agent`` selects the programmatic agent defined in ``custom_agents``; ``prompt``
    is its full composed system prompt (from the graph SSOT).
    """
    cfg = build_custom_agent(
        name=agent,
        prompt=prompt,
        description=description,
        display_name=display_name,
        tools=effective_agent_tools(tools, submission_enabled=submission_enabled),
        mcp_servers=mcp_servers,
    )
    kwargs: dict[str, Any] = {
        "agent": agent,
        "custom_agents": [cfg],
        "enable_config_discovery": False,
        "skip_custom_instructions": True,
        "enable_skills": False,
        "custom_agents_local_only": True,
    }
    if execution_policy is not None:
        kwargs["hooks"] = tool_deadline_hooks(execution_policy)
    if model:
        kwargs["model"] = model
    if cwd is not None:
        kwargs["working_directory"] = cwd
    return kwargs
