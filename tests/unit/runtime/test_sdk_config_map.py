"""Unit tests for SDK custom-agent and session configuration."""

from __future__ import annotations

from roundtable.backend import ExecutionPolicy
from roundtable.backend.sdk.config_map import build_session_kwargs


def test_build_session_kwargs_maps_graph_fields_to_custom_agent() -> None:
    servers = {"fs": {"command": "x"}}
    kwargs = build_session_kwargs(
        agent="security-review",
        prompt="You are the security reviewer.",
        description="Finds vulns.",
        display_name="Security Review",
        tools=("read", "fs/get_file"),
        model="claude-opus-4.8",
        mcp_servers=servers,
        cwd="/repo",
    )

    assert kwargs["custom_agents"] == [
        {
            "name": "security-review",
            "prompt": "You are the security reviewer.",
            "infer": False,
            "description": "Finds vulns.",
            "display_name": "Security Review",
            "tools": ["read", "fs/get_file"],
            "mcp_servers": servers,
        }
    ]
    assert kwargs["agent"] == "security-review"
    assert kwargs["working_directory"] == "/repo"


def test_model_binds_at_the_session_not_the_custom_agent() -> None:
    """The custom-agent ``model:`` only steers a *sub*-agent, and the runtime falls
    back to the parent model rather than refusing an id it cannot honour. Roundtable
    runs each agent as the session's primary agent, so declaring it there ran the
    ambient default while every artifact echoed the declaration."""
    kwargs = build_session_kwargs(agent="a", prompt="body", model="claude-opus-4.8")
    assert kwargs["model"] == "claude-opus-4.8"
    assert "model" not in kwargs["custom_agents"][0]


def test_an_undeclared_model_leaves_the_choice_to_the_runtime() -> None:
    kwargs = build_session_kwargs(agent="a", prompt="body")
    assert "model" not in kwargs


def test_omitted_tools_leave_sdk_surface_unrestricted() -> None:
    custom_agent = build_session_kwargs(agent="a", prompt="body")["custom_agents"][0]
    assert "tools" not in custom_agent


def test_empty_tools_are_preserved() -> None:
    custom_agent = build_session_kwargs(agent="a", prompt="body", tools=())["custom_agents"][0]
    assert custom_agent["tools"] == []


def test_submission_transport_is_appended_to_explicit_allowlist() -> None:
    custom_agent = build_session_kwargs(
        agent="a",
        prompt="body",
        tools=("read",),
        submission_enabled=True,
    )["custom_agents"][0]
    assert custom_agent["tools"] == ["read", "roundtable_submit_output"]


def test_target_repository_injection_is_disabled() -> None:
    kwargs = build_session_kwargs(agent="a", prompt="body", cwd="/untrusted/repo")
    assert kwargs["enable_config_discovery"] is False
    assert kwargs["skip_custom_instructions"] is True
    assert kwargs["enable_skills"] is False
    assert kwargs["custom_agents_local_only"] is True


def test_mcp_servers_belong_to_custom_agent_not_session() -> None:
    servers = {"fs": {"command": "x"}}
    kwargs = build_session_kwargs(agent="a", prompt="body", mcp_servers=servers)
    assert kwargs["custom_agents"][0]["mcp_servers"] == servers
    assert "mcp_servers" not in kwargs


def test_declared_execution_policy_adds_hooks_independent_of_agent_name() -> None:
    policy = ExecutionPolicy(120, 30, False)
    kwargs = build_session_kwargs(
        agent="any-agent",
        prompt="body",
        execution_policy=policy,
    )
    assert callable(kwargs["hooks"]["on_pre_tool_use"])


def test_policy_free_agent_receives_no_hooks() -> None:
    kwargs = build_session_kwargs(agent="redgreen", prompt="body")
    assert "hooks" not in kwargs
