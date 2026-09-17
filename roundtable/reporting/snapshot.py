"""Config-agnostic graph-enrichment snapshot for the offline report.

The report decorates each agent node with *static* graph metadata — emoji,
runtime class, dependencies, delivery label, and a one-line description.
Historically the loader read this **live** from the engine's configuration at
report-generation time, which (a) coupled the report to one specific
configuration and (b) rendered a *historical* session with the *current* graph
(silent drift). This module defines the serialized, engine-neutral form of that
metadata: a ``graph.json`` snapshot the run path writes once — point-in-time
correct — and the report reads back. Any engine that emits this shape gets a
report, with **no** import of engine config from the ``reporting`` package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

SNAPSHOT_FILENAME = "graph.json"
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AgentInfo:
    """Static, engine-neutral enrichment for one agent node."""

    key: str
    emoji: str = ""
    runtime: str = "llm"  # 'llm' | 'deterministic' | 'unknown'
    required_deps: tuple[str, ...] = ()
    optional_deps: tuple[str, ...] = ()
    delivery_label: str | None = None
    description: str | None = None
    display_name: str | None = None  # human-readable report label (fallback: key)
    # The graph's authored tool grant (concrete runtime tool names and/or
    # ``server/tool`` MCP entries). Empty means either "no grant declared" or a
    # legacy session written before this field existed — the two are
    # indistinguishable, so a reader must treat empty as "unknown", never as
    # "nothing was granted".
    declared_tools: tuple[str, ...] = ()
    # The ``server/tool`` entries above under the hyphenated name the runtime
    # reports a call as (``ado-code-read-repo_get_file_content``). Lets a reader
    # match an observed name against this agent's grant without re-deriving it.
    declared_mcp_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphSnapshot:
    """The point-in-time graph metadata for a session, keyed by agent key."""

    agents: dict[str, AgentInfo] = field(default_factory=dict)
    display_name: str | None = None  # the configuration's name (report title)
    # Every MCP tool the registry could provision for ANY agent, CLI-named. The
    # universe that separates "this agent was not granted it" from "no server
    # here serves it at all" — the second is unattributed, never a violation.
    mcp_tool_inventory: tuple[str, ...] = ()

    def label_to_producer(self) -> dict[str, str]:
        """Map every delivery label (default + custom) to its producing agent key."""
        mapping: dict[str, str] = {}
        for info in self.agents.values():
            mapping[f"Context from {info.key}"] = info.key
            if info.delivery_label:
                label = info.delivery_label.lstrip("#").strip()
                if label:
                    mapping[label] = info.key
        return mapping


class GraphProvider(Protocol):
    """A zero-arg callable yielding a live snapshot for legacy sessions.

    Injected at the CLI seam so the ``reporting`` package itself imports no
    engine configuration; returns ``None`` when it cannot produce one.
    """

    def __call__(self) -> GraphSnapshot | None: ...


def to_payload(snapshot: GraphSnapshot) -> dict:
    """Serialize a snapshot to the ``graph.json`` payload (camelCase, like siblings)."""
    return {
        "version": _SCHEMA_VERSION,
        "displayName": snapshot.display_name,
        "mcpToolInventory": list(snapshot.mcp_tool_inventory),
        "agents": [
            {
                "key": a.key,
                "emoji": a.emoji,
                "runtime": a.runtime,
                "requiredDeps": list(a.required_deps),
                "optionalDeps": list(a.optional_deps),
                "deliveryLabel": a.delivery_label,
                "description": a.description,
                "displayName": a.display_name,
                "declaredTools": list(a.declared_tools),
                "declaredMcpTools": list(a.declared_mcp_tools),
            }
            for a in snapshot.agents.values()
        ],
    }


def from_payload(payload: dict) -> GraphSnapshot:
    """Parse a ``graph.json`` payload into a :class:`GraphSnapshot` (null-safe)."""
    agents: dict[str, AgentInfo] = {}
    for a in payload.get("agents") or []:
        if not isinstance(a, dict):
            continue
        key = a.get("key")
        if not key:
            continue
        agents[key] = AgentInfo(
            key=key,
            emoji=a.get("emoji") or "",
            runtime=a.get("runtime") or "unknown",
            required_deps=tuple(a.get("requiredDeps") or ()),
            optional_deps=tuple(a.get("optionalDeps") or ()),
            delivery_label=a.get("deliveryLabel"),
            description=a.get("description"),
            display_name=a.get("displayName"),
            declared_tools=tuple(a.get("declaredTools") or ()),
            declared_mcp_tools=tuple(a.get("declaredMcpTools") or ()),
        )
    return GraphSnapshot(
        agents=agents,
        display_name=payload.get("displayName"),
        mcp_tool_inventory=tuple(payload.get("mcpToolInventory") or ()),
    )


def snapshot_to_json(snapshot: GraphSnapshot) -> str:
    """The ``graph.json`` document text for one snapshot."""
    return json.dumps(to_payload(snapshot), indent=2, ensure_ascii=False)


def load_graph_snapshot(session_dir: str | Path) -> GraphSnapshot | None:
    """Read a session's persisted ``graph.json``, or ``None`` when absent/unreadable."""
    path = Path(session_dir) / SNAPSHOT_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return from_payload(payload)
