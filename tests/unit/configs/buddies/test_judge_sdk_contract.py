"""Judge's graph-owned SDK capability contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from roundtable.bundle.paths import set_config_root
from roundtable.context.ado_context import render_ado_context_for_servers
from roundtable.graph.loader import load_agent_graph
from roundtable.inputs.ado_identity import AdoIdentity
from roundtable.mcp.registry import McpBuildContext, resolve_mcp_config
from roundtable.runtime.agent_setup import graph_custom_agents

_BUNDLE = Path("roundtable/configs/buddies")
_PROMPTS = _BUNDLE / "prompts" / "Reviewer"
_ADO_TOOLS = (
    "ado-code-read/repo_get_file_content",
    "ado-code-read/repo_list_directory",
    "ado-code-read/repo_search_commits",
    "ado-code-read/wit_get_work_item",
    "ado-code-read/wiki_get_page_content",
)


@pytest.fixture(autouse=True)
def _use_buddies_config():
    set_config_root(_BUNDLE)
    try:
        yield
    finally:
        set_config_root(None)


def _judge():
    entries = load_agent_graph(_BUNDLE / "agent_graph.yaml")
    return graph_custom_agents(_PROMPTS, entries=entries)["Judge"]


def _identity() -> AdoIdentity:
    return AdoIdentity(
        org="contoso",
        project="ExampleProject",
        repo_name="Repo",
        remote_url="https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo",
        host="dev.azure.com",
    )


def test_judge_sdk_contract_is_read_only_and_explicit() -> None:
    judge = _judge()

    assert judge.tools == ("view", "rg", "glob", "web_fetch", *_ADO_TOOLS)
    # No write and no shell: the Judge adjudicates from reviewer output.
    assert not {"apply_patch", "powershell"} & set(judge.tools)
    entries = load_agent_graph(_BUNDLE / "agent_graph.yaml")
    judge_entry = next(entry for entry in entries if entry.key == "Judge")
    assert judge_entry.mcp_server_names == ("ado-code-read",)


def test_judge_ado_capabilities_render_only_for_resolved_server() -> None:
    judge = _judge()
    unavailable = resolve_mcp_config(["ado-code-read"], McpBuildContext())
    available = resolve_mcp_config(
        ["ado-code-read"],
        McpBuildContext(ado_org="contoso"),
    )

    assert unavailable is None
    assert render_ado_context_for_servers([_identity()], [], judge.tools or ()) == ""
    assert available is not None
    rendered = render_ado_context_for_servers(
        [_identity()],
        list(available),
        judge.tools or (),
    )
    for tool in _ADO_TOOLS:
        assert tool.replace("/", "-", 1) in rendered
    assert "ado-code-read-repo_get_pull_request_by_id" not in rendered
