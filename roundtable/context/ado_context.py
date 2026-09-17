"""ado_context: the two ADO context sections — Repository Identity + Tool Bindings.

Renders the per-agent ADO context block (``## ADO Repository Identity`` +
``## ADO Tool Bindings``) injected into the context of agents that OPT IN to an
ADO server (declare an ``ado-*`` server under ``mcp:`` in ``agent_graph.yaml``),
when ADO MCP is enabled and ≥1 identity resolved. The block is rendered PER
AGENT — only the servers the agent declared contribute Tool Bindings, so each
agent sees exactly the tools it may call (least privilege). The Identity section
is repo-fact (server-agnostic) and identical across ADO agents.

The Copilot CLI namespaces MCP tools as ``<serverName>-<toolName>`` (verified:
the invoked tool was
``ado-work-items-repo_get_pull_request_by_id``, NOT the bare tool). So the **Tool
name** column emits **server-prefixed** names; otherwise agents would call a name
that does not resolve under the CLI. The advertised ``(capability, bare tool)``
rows are the SSOT ``bindings:`` blocks in ``mcp_servers.yaml`` (one data edit per
domain), read via ``runtime/mcp_registry.server_bindings``.
"""

from __future__ import annotations

from collections.abc import Sequence

from roundtable.inputs import AdoIdentity
from roundtable.mcp import server_bindings

__all__ = [
    "ADO_SECTION_HEADINGS",
    "prefixed_tool_name",
    "render_ado_context_for_servers",
    "render_ado_identity_section",
    "render_ado_tool_bindings_section",
]

ADO_SECTION_HEADINGS = {
    "identity": "## ADO Repository Identity (USE THESE FOR MCP TOOL CALLS)",
    "tool_bindings": "## ADO Tool Bindings",
}


def prefixed_tool_name(bare: str, *, server_name: str) -> str:
    """Apply the CLI's ``<server>-<tool>`` namespace to a bare tool name.

    Idempotent: a name already carrying the prefix is returned unchanged.
    """
    pfx = f"{server_name}-"
    return bare if bare.startswith(pfx) else f"{pfx}{bare}"


def render_ado_identity_section(identities: Sequence[AdoIdentity]) -> str:
    """Render the ``## ADO Repository Identity`` section."""
    if not identities:
        return "\n".join(
            [
                ADO_SECTION_HEADINGS["identity"],
                "> No ADO repository identity resolved (non-PR mode).",
            ]
        )
    ado_lines = []
    for ident in identities:
        proj_display = (
            f"`{ident.project_id}` (name: {ident.project})"
            if ident.project_id
            else f"`{ident.project}`"
        )
        repo_display = (
            f"`{ident.repository_id}` (name: {ident.repo_name})"
            if ident.repository_id
            else f"`{ident.repo_name}`"
        )
        ado_lines.append(
            f"- **Project**: {proj_display}  |  **Repository**: {repo_display}  "
            f"|  Remote: {ident.remote_url}"
        )
    return "\n".join(
        [
            ADO_SECTION_HEADINGS["identity"],
            "> \u26a0\ufe0f Always use the **GUID values** shown below when calling "
            "MCP tools. GUIDs are preferred over names for reliability. The ADO "
            "**project** name often differs from the repository name. Never guess "
            "from the folder name.",
            "",
            "\n".join(ado_lines),
        ]
    )


def render_ado_tool_bindings_section(
    server_names: Sequence[str],
    allowed_tools: Sequence[str],
) -> str:
    """Render the ``## ADO Tool Bindings`` table for the agent's ADO servers.

    Rows are the union of each server's ``bindings:`` (SSOT: ``mcp_servers.yaml``,
    read via ``mcp_registry.server_bindings``), each tool CLI-prefixed with its
    OWN ``<server>-`` namespace. ``server_names`` is iterated in the given order;
    an agent with one ADO server yields only bindings also present in its
    graph-owned ``tools`` allowlist as ``<server>/<bare-tool>``.
    """
    allowed = set(allowed_tools)
    rows: list[tuple[str, str]] = []
    for server_name in server_names:
        for cap, bare in server_bindings(server_name):
            if f"{server_name}/{bare}" in allowed:
                rows.append((cap, prefixed_tool_name(bare, server_name=server_name)))
    table_lines = [
        "| Capability | Tool name |",
        "|------------|-----------|",
        *(f"| {cap} | `{tool}` |" for cap, tool in rows),
    ]
    return "\n".join(
        [
            ADO_SECTION_HEADINGS["tool_bindings"],
            "> Wired ADO MCP tool names for this agent. Use the `Tool name` "
            "column verbatim when invoking via the SDK.",
            "",
            "\n".join(table_lines),
        ]
    )


def render_ado_context_for_servers(
    identities: Sequence[AdoIdentity],
    server_names: Sequence[str],
    allowed_tools: Sequence[str],
) -> str:
    """The per-agent ADO context block: Identity + Tool Bindings for ``server_names``.

    Returns ``""`` when the agent declares no ADO server (``server_names`` empty)
    — non-ADO agents get no ADO context at all. The Identity section is
    server-agnostic (repo facts); Tool Bindings lists only the declared servers'
    tools granted by ``allowed_tools``. Sections are ``\\n\\n``-joined and
    terminated with a trailing blank so the caller can concatenate the next
    section directly.
    """
    if not server_names:
        return ""
    identity = render_ado_identity_section(identities)
    bindings = render_ado_tool_bindings_section(server_names, allowed_tools)
    return f"{identity}\n\n{bindings}\n\n"
