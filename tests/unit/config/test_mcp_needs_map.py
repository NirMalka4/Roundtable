"""Unit tests for ``agent_setup.load_mcp_needs_map`` (Tier 1 per-agent MCP selection).

Post-consolidation the map is derived SOLELY from the graph SSOT
(``agent_graph.yaml`` → ``get_configuration().entries``). A non-empty ``mcp`` binding
list is keyed by ``GraphEntry.key`` (via the derived ``mcp_server_names``); an
empty/absent ``mcp`` is omitted (servers are opt-in, ``defaults: []``). The
``validate_agents`` registry check for unknown servers is covered in
``test_validate_agents.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

from roundtable.graph.model import McpBinding
from roundtable.runtime.agent_setup import load_mcp_needs_map


def _entry(key, *, is_llm=True, mcp=()):
    bindings = tuple(McpBinding(server=s) for s in mcp)
    return SimpleNamespace(
        key=key,
        is_llm=is_llm,
        model=(),
        mcp=bindings,
        mcp_server_names=tuple(b.server for b in bindings),
    )


def test_declared_servers_keyed_by_graph_key():
    entries = (_entry("Sec", mcp=["ado-work-items", "kusto"]),)
    assert load_mcp_needs_map(entries=entries)["Sec"] == ["ado-work-items", "kusto"]


def test_empty_and_non_llm_entries_omitted():
    out = load_mcp_needs_map(
        entries=(
            _entry("Plain", mcp=[]),  # no opt-in servers => omitted
            _entry("NotLlm", is_llm=False, mcp=["ado-work-items"]),
            _entry("Sec", mcp=["kusto"]),
        )
    )
    assert out == {"Sec": ["kusto"]}
    assert "Plain" not in out
