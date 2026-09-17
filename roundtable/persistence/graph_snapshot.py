"""Bridge: snapshot the LIVE engine graph configuration into ``graph.json``.

This is the **one** place that reads the engine's configuration to produce the
engine-neutral :class:`~roundtable.reporting.snapshot.GraphSnapshot` the
offline report consumes. Writing it at persist time makes the report's
enrichment point-in-time correct (no drift against a later-edited graph) and
keeps the ``reporting`` package free of any engine-config import — the report
reads the artifact, never the config.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from roundtable.reporting import AgentInfo, GraphSnapshot

if TYPE_CHECKING:
    from roundtable.graph import Configuration


def _describe(prompt_path: str, prompt_root: object | None = None) -> str | None:
    """The authored ``description:`` frontmatter of an agent's ``.agent.md`` body.

    The single source of truth for "what this agent does". Absent (empty) for
    deterministic nodes, which carry no prompt body — the report supplies a
    generic note for those at render time.
    """
    if not prompt_path:
        return None
    try:
        from roundtable.bundle import bundle_root
        from roundtable.runtime import parse_agent_file

        root = Path(str(prompt_root)) if prompt_root is not None else bundle_root()
        fm, _body = parse_agent_file((root / prompt_path).read_text("utf-8"))
        desc = str(fm.get("description") or "").strip()
        return " ".join(desc.split()) if desc else None  # collapse YAML soft-wrap
    except (OSError, ValueError, KeyError, ImportError):
        return None


def _expand_mcp_grant(tools: tuple[str, ...] | None) -> tuple[str, ...]:
    """The agent's ``<server>/<tool>`` grant entries, under their CLI-visible names.

    Only entries naming a registered server's real tool are expanded; anything
    else is dropped rather than guessed at, so an unrecognised entry can never
    become a name an observed call is later measured against.
    """
    from roundtable.mcp import cli_tool_name, server_tool_names

    names: list[str] = []
    for entry in tools or ():
        server, sep, tool = entry.partition("/")
        if sep and tool in server_tool_names(server):
            names.append(cli_tool_name(server, tool))
    return tuple(names)


def build_graph_snapshot(configuration: Configuration | None = None) -> GraphSnapshot:
    """Capture the current agent graph as an engine-neutral snapshot."""
    from roundtable.graph import get_configuration
    from roundtable.mcp import cli_tool_inventory

    cfg = configuration or get_configuration()
    prompt_root = cfg.root / "prompts" / "Reviewer"
    agents: dict[str, AgentInfo] = {}
    for entry in cfg.entries:
        agents[entry.key] = AgentInfo(
            key=entry.key,
            emoji=entry.emoji,
            runtime="llm" if entry.is_llm else "deterministic",
            required_deps=tuple(entry.required_dep_keys),
            optional_deps=tuple(entry.optional_dep_keys),
            delivery_label=entry.delivery_label,
            description=_describe(entry.prompt_path, prompt_root),
            display_name=entry.display_name,
            declared_tools=tuple(entry.tools or ()),
            declared_mcp_tools=_expand_mcp_grant(entry.tools),
        )
    return GraphSnapshot(
        agents=agents,
        display_name=cfg.name,
        mcp_tool_inventory=tuple(sorted(cli_tool_inventory())),
    )
