"""Review-domain agent input assembly for the generic Engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from roundtable.context import (
    build_injection_sections,
    render_git_context_section,
    resolve_corpus,
)
from roundtable.engine import AgentInput, AgentRunOutcome
from roundtable.extraction import extract_findings
from roundtable.graph import Configuration, GraphEntry
from roundtable.grounding import ground_source_excerpts
from roundtable.result_access import response_of
from roundtable.runtime import is_judge_like_agent, try_resolve_canonical_id


def _history_section(history: str) -> str:
    body = history.strip()
    if not body:
        return ""
    return "\n\n---\n## Git History (git log --oneline -10 per changed file)\n" + body


def _upstream_finding_ids(
    entry: GraphEntry,
    snapshot: Mapping[str, AgentRunOutcome],
    configuration: Configuration,
) -> list[str]:
    """The ``agent::finding_id`` universe an adjudicator must account for.

    Only LLM nodes originate findings. A deterministic node may re-render a reviewer's
    findings verbatim, and admitting its echo would enter the same finding twice under a
    second producer — forcing the adjudicator to cite a projection as though it were an
    independent reviewer in order to satisfy the coverage gate.
    """
    identifiers: list[str] = []
    seen: set[str] = set()
    for name, outcome in snapshot.items():
        if name == entry.key or is_judge_like_agent(name, configuration):
            continue
        producer = configuration.by_key.get(name)
        if producer is not None and not producer.is_llm:
            continue
        response = response_of(outcome)
        if response is None:
            continue
        canonical = try_resolve_canonical_id(name, configuration) or name.lower()
        for finding in extract_findings(name, response, configuration):
            if not finding.id:
                continue
            key = f"{canonical}::{finding.id}"
            if key not in seen:
                seen.add(key)
                identifiers.append(key)
    return identifiers


@dataclass(frozen=True)
class ReviewAgentInputBuilder:
    configuration: Configuration
    session_header: str
    context_by_key: Mapping[str, str]
    changed_files: tuple[str, ...]
    workspace: str | None

    def __call__(
        self,
        entry: GraphEntry,
        snapshot: Mapping[str, AgentRunOutcome],
        scheduled: frozenset[str],
    ) -> AgentInput:
        corpus = resolve_corpus(entry, snapshot, self.configuration)
        context = (
            self.session_header
            + self.context_by_key.get(entry.key, "")
            + render_git_context_section(entry, corpus.diff)
            + _history_section(corpus.git_history)
            + build_injection_sections(
                entry,
                snapshot,
                scheduled,
                self.configuration,
            )
        )
        validation_context = {
            "changed_files": list(self.changed_files),
            "upstream_finding_ids": _upstream_finding_ids(
                entry,
                snapshot,
                self.configuration,
            ),
            "upstream_responses": {
                edge.source: response
                for edge in entry.forward_edges
                if edge.source in scheduled
                and (response := response_of(snapshot.get(edge.source))) is not None
            },
        }

        def finalize(response: str) -> str:
            return ground_source_excerpts(
                response,
                entry.output_schema,
                self.workspace,
                self.configuration.root / "schemas",
            )

        return AgentInput(context, validation_context, finalize)
