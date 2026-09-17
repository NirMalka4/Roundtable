"""The graph → ``graph.json`` snapshot, and the MCP grant expansion it carries.

The expansion is what makes "this agent called an MCP tool it was not granted" a
provable statement rather than a guess, so these pin both halves of it: a real
grant becomes the exact name a call would carry, and anything unrecognised is
dropped rather than turned into a name an observed call is measured against.
"""

from __future__ import annotations

from roundtable.mcp.registry import cli_tool_inventory
from roundtable.persistence.graph_snapshot import _expand_mcp_grant, build_graph_snapshot


def test_a_real_grant_expands_to_the_name_a_call_would_carry() -> None:
    assert _expand_mcp_grant(("ado-work-items/wit_get_work_item",)) == (
        "ado-work-items-wit_get_work_item",
    )


def test_capability_classes_are_not_mcp_grants() -> None:
    assert _expand_mcp_grant(("read", "search", "execute", "web")) == ()


def test_an_unknown_server_or_tool_is_dropped_not_guessed_at() -> None:
    """A fabricated name would be something an observed call is judged against."""
    assert _expand_mcp_grant(("no-such-server/some_tool", "ado-work-items/no_such_tool")) == ()


def test_every_expanded_name_is_in_the_registry_inventory() -> None:
    """The verdict compares against the inventory, so an expansion outside it is dead."""
    inventory = cli_tool_inventory()
    for agent in build_graph_snapshot().agents.values():
        assert set(agent.declared_mcp_tools) <= inventory


def test_the_snapshot_carries_the_inventory_the_verdict_needs() -> None:
    snap = build_graph_snapshot()
    assert set(snap.mcp_tool_inventory) == cli_tool_inventory()


def test_an_agents_mcp_grant_counts_exactly_its_slash_form_declarations() -> None:
    """Every ``<server>/<tool>`` entry in a live bundle must survive expansion.

    A silently dropped grant would read as "never granted" and turn a legitimate
    call into a reported violation.
    """
    from roundtable.mcp.registry import server_tool_names

    for agent in build_graph_snapshot().agents.values():
        expected = [
            entry
            for entry in agent.declared_tools
            if "/" in entry and entry.split("/", 1)[1] in server_tool_names(entry.split("/", 1)[0])
        ]
        assert len(agent.declared_mcp_tools) == len(expected)
