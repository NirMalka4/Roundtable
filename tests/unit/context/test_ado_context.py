"""Golden-string tests for the per-agent ADO context sections (MCP consolidation).

The Tool Bindings rows are sourced per-server from the ``bindings:`` meta in
``mcp_servers.yaml`` (via ``mcp_registry.server_bindings``); the Tool name column
is namespaced with each server's own ``<server>-`` prefix per the CLI namespace
finding.
"""

from __future__ import annotations

import pytest

from roundtable.context.ado_context import (
    prefixed_tool_name,
    render_ado_context_for_servers,
    render_ado_identity_section,
    render_ado_tool_bindings_section,
)
from roundtable.context.session_header import (
    DiffStats,
    SessionHeaderInputs,
    build_session_header,
)
from roundtable.inputs.ado_identity import AdoIdentity

_WARN = (
    "> \u26a0\ufe0f Always use the **GUID values** shown below when calling MCP "
    "tools. GUIDs are preferred over names for reliability. The ADO **project** "
    "name often differs from the repository name. Never guess from the folder name."
)

_WORK_ITEMS_BINDINGS = """## ADO Tool Bindings
> Wired ADO MCP tool names for this agent. Use the `Tool name` column verbatim when invoking via the SDK.

| Capability | Tool name |
|------------|-----------|
| Get pull request | `ado-work-items-repo_get_pull_request_by_id` |
| Find PR for branch | `ado-work-items-repo_list_pull_requests_by_repo_or_project` |
| List PR threads | `ado-work-items-repo_list_pull_request_threads` |
| List PR thread comments | `ado-work-items-repo_list_pull_request_thread_comments` |
| Get work item | `ado-work-items-wit_get_work_item` |
| Batch get work items | `ado-work-items-wit_get_work_items_batch_by_ids` |"""


def test_identity_empty_placeholder():
    out = render_ado_identity_section([])
    assert out == (
        "## ADO Repository Identity (USE THESE FOR MCP TOOL CALLS)\n"
        "> No ADO repository identity resolved (non-PR mode)."
    )


def test_identity_with_guids():
    ident = AdoIdentity(
        org="contoso",
        project="ExampleProject",
        repo_name="ExampleRepo",
        remote_url="https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo",
        host="dev.azure.com",
        repository_id="repo-guid",
        project_id="proj-guid",
    )
    out = render_ado_identity_section([ident])
    assert out == (
        "## ADO Repository Identity (USE THESE FOR MCP TOOL CALLS)\n"
        f"{_WARN}\n"
        "\n"
        "- **Project**: `proj-guid` (name: ExampleProject)  |  **Repository**: "
        "`repo-guid` (name: ExampleRepo)  |  Remote: "
        "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo"
    )


def test_identity_without_guids_uses_bare_names():
    ident = AdoIdentity(
        org="o",
        project="ExampleProject",
        repo_name="ExampleRepo",
        remote_url="https://x/_git/ExampleRepo",
        host="dev.azure.com",
    )
    out = render_ado_identity_section([ident])
    assert "**Project**: `ExampleProject`  |  **Repository**: `ExampleRepo`" in out
    assert "(name:" not in out


def test_tool_bindings_work_items_server():
    allowed = [
        f"ado-work-items/{tool}"
        for tool in (
            "repo_get_pull_request_by_id",
            "repo_list_pull_requests_by_repo_or_project",
            "repo_list_pull_request_threads",
            "repo_list_pull_request_thread_comments",
            "wit_get_work_item",
            "wit_get_work_items_batch_by_ids",
        )
    ]
    assert render_ado_tool_bindings_section(["ado-work-items"], allowed) == _WORK_ITEMS_BINDINGS


def test_tool_bindings_publish_server_prefixed():
    out = render_ado_tool_bindings_section(
        ["ado-publish"],
        [
            "ado-publish/repo_get_repo_by_name_or_id",
            "ado-publish/repo_create_pull_request",
        ],
    )
    assert "| Lookup repository | `ado-publish-repo_get_repo_by_name_or_id` |" in out
    assert "| Create draft PR | `ado-publish-repo_create_pull_request` |" in out
    assert "Update pull request" not in out


def test_prefixed_tool_name_idempotent():
    assert prefixed_tool_name("repo_x", server_name="ado-work-items") == "ado-work-items-repo_x"
    assert (
        prefixed_tool_name("ado-work-items-repo_x", server_name="ado-work-items")
        == "ado-work-items-repo_x"
    )


def _inputs():
    return SessionHeaderInputs(
        target_branch="dev",
        source_branch="feat",
        source_sha="abc",
        diff_stats=DiffStats(1, 2, 3),
        pr_id=42,
        pr_title="T",
    )


def test_session_header_is_metadata_only():
    out = build_session_header(_inputs())
    assert out.startswith("## Change Under Review")
    assert "## ADO Repository Identity" not in out
    assert "## Branch Metadata" not in out


def test_render_ado_context_empty_when_no_servers():
    assert render_ado_context_for_servers([], [], ["ado-work-items/wit_get_work_item"]) == ""


@pytest.mark.parametrize(
    ("allowed_tools", "expected_binding"),
    [
        (["ado-work-items/wit_get_work_item"], "Get work item"),
        ([], None),
    ],
)
def test_render_ado_context_identity_survives_binding_filter(allowed_tools, expected_binding):
    ident = AdoIdentity(
        org="contoso",
        project="ExampleProject",
        repo_name="ExampleRepo",
        remote_url="u",
        host="dev.azure.com",
    )
    out = render_ado_context_for_servers([ident], ["ado-work-items"], allowed_tools)
    i_id = out.index("## ADO Repository Identity")
    i_tb = out.index("## ADO Tool Bindings")
    assert i_id < i_tb
    assert ("Get work item" in out) is (expected_binding is not None)
    assert "Get pull request" not in out
    assert out.endswith("\n\n")


def test_render_ado_context_no_identity_uses_placeholder():
    out = render_ado_context_for_servers(
        [], ["ado-work-items"], ["ado-work-items/wit_get_work_item"]
    )
    assert "> No ADO repository identity resolved (non-PR mode)." in out
    assert "## ADO Tool Bindings" in out
