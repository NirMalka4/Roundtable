"""Typed per-agent input supplied by an engine caller."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from roundtable.graph import GraphEntry

from .agent_runner import AgentRunOutcome


def _unchanged(response: str) -> str:
    return response


@dataclass(frozen=True)
class AgentInput:
    context: str
    validation_context: Mapping[str, Any] = field(default_factory=dict)
    finalize_response: Callable[[str], str] = _unchanged


class AgentInputBuilder(Protocol):
    def __call__(
        self,
        entry: GraphEntry,
        snapshot: Mapping[str, AgentRunOutcome],
        scheduled: frozenset[str],
    ) -> AgentInput: ...


def default_agent_input(
    entry: GraphEntry,
    snapshot: Mapping[str, AgentRunOutcome],
    scheduled: frozenset[str],
) -> AgentInput:
    sections: list[str] = []
    for edge in entry.forward_edges:
        if edge.source not in scheduled:
            continue
        outcome = snapshot.get(edge.source)
        if outcome is not None and outcome.valid and outcome.response:
            sections.append(outcome.response)
    return AgentInput("\n\n".join(sections))
