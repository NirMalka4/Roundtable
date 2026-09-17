"""Per-agent tool surface: what the graph granted, what the run actually used.

A change to the tool surface is invisible in the graph. Swapping the SDK's search
tools for a shell, for instance, leaves every ``tools:`` declaration byte-identical
while an agent's round count triples — so a declared inventory has **zero**
detection power over it. What does have detection power is the *observed*
composition, recorded per run and compared across runs.

This module reads both sides out of the artifacts a session already writes and
diffs two runs. It is config-agnostic: the declared grant arrives through the
``graph.json`` snapshot, never from live config.

**What this can now rule on.** The graph grants *concrete* runtime tool names
(``view``, ``rg``, ``powershell``) validated against the probed inventory in
``capabilities/runtime_inventory.yaml``, and MCP tools whose ``<server>/<tool>`` grant
expands to exactly the names a call carries. Grant and observation are therefore
in one vocabulary, and "used outside its grant" is a single provable verdict over
both kinds — no capability-class mapping to guess at.

Tools in the inventory's ``always_on`` set are exempt. Calling one is never a
violation because no graph grant could have prevented it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from roundtable.capabilities import always_on_tool_names

from .loader import ReportModel, load_report_model


@dataclass(frozen=True)
class AgentToolSurface:
    """One agent's granted-versus-used tool surface for a single run."""

    key: str
    declared: tuple[str, ...] = ()
    declared_mcp: tuple[str, ...] = ()
    used: dict[str, int] = field(default_factory=dict)

    @property
    def grant_known(self) -> bool:
        """Whether the run recorded a grant at all (legacy sessions did not).

        An empty grant is also how an *unrestricted* agent serializes, so silence
        here is the only safe reading — never "it was granted nothing".
        """
        return bool(self.declared)

    @property
    def total_calls(self) -> int:
        return sum(self.used.values())

    @property
    def granted(self) -> frozenset[str]:
        """Every tool name this agent may call, built-in and MCP alike."""
        return frozenset(self.declared) | frozenset(self.declared_mcp)

    @property
    def used_outside_grant(self) -> tuple[str, ...]:
        """Calls the agent's own grant does not cover — always-on tools excepted."""
        if not self.grant_known:
            return ()
        allowed = self.granted | always_on_tool_names()
        return tuple(sorted(t for t in self.used if t not in allowed))

    @property
    def granted_unused(self) -> tuple[str, ...]:
        """Granted tools this run never reached — a grant that bought nothing.

        One run, so this is an observation and not yet a case for narrowing.
        """
        return tuple(sorted(t for t in self.granted if t not in self.used))


@dataclass(frozen=True)
class ToolSurfaceDelta:
    """How one agent's observed tool composition moved between two runs."""

    key: str
    added: tuple[str, ...] = ()  # used in the later run, absent from the earlier
    removed: tuple[str, ...] = ()  # used in the earlier run, absent from the later
    calls_before: int = 0
    calls_after: int = 0

    @property
    def shifted(self) -> bool:
        return bool(self.added or self.removed)


def read_tool_surface(session_dir: str | Path) -> dict[str, AgentToolSurface]:
    """The granted-versus-used tool surface of every agent in one session."""
    return _from_model(load_report_model(session_dir))


def _from_model(model: ReportModel) -> dict[str, AgentToolSurface]:
    return {
        node.key: AgentToolSurface(
            key=node.key,
            declared=tuple(node.declared_tools),
            declared_mcp=tuple(node.declared_mcp_tools),
            used=dict(node.tool_stats),
        )
        for node in model.nodes
    }


def diff_tool_surface(
    before: dict[str, AgentToolSurface], after: dict[str, AgentToolSurface]
) -> list[ToolSurfaceDelta]:
    """Composition shifts between two runs, for agents present in both.

    An agent missing from either run is skipped rather than reported as a total
    swing: it did not run, which is a coverage fact, not a tool-surface one.
    """
    deltas: list[ToolSurfaceDelta] = []
    for key in sorted(set(before) & set(after)):
        old, new = before[key], after[key]
        added = tuple(sorted(set(new.used) - set(old.used)))
        removed = tuple(sorted(set(old.used) - set(new.used)))
        deltas.append(
            ToolSurfaceDelta(
                key=key,
                added=added,
                removed=removed,
                calls_before=old.total_calls,
                calls_after=new.total_calls,
            )
        )
    return deltas
