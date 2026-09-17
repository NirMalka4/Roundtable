"""peer_finding_index: canonical index of the peer reviewers' findings.

Builds a queryable index of every reviewer finding in the session, keyed by
``{canonical_agent_id}::{finding_id}``, so the consolidated dossier's entries stay
addressable back to the originating reviewer finding record (locations, suggestions,
evidence) — the Count Checksum the Judge accounts against.

Only LLM reviewer nodes are indexed. A deterministic node may re-render reviewer findings
verbatim (the claim-only projection does exactly that), and indexing its echo would both
double the checksum and let the Judge cite a projection as an independent source. Judge-like
agents are skipped as well — the Judge cannot reference its own output. First-writer wins on
duplicate composite keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from roundtable.extraction import FindingItem, extract_findings
from roundtable.graph import Configuration
from roundtable.result_access import response_of
from roundtable.runtime import is_judge_like_agent, try_resolve_canonical_id

from .configuration import buddies_configuration


@dataclass(frozen=True)
class PeerFindingRecord:
    """A single reviewer finding indexed for dossier resolution."""

    source_agent: str
    source_agent_canonical: str
    finding_id: str
    finding: FindingItem


@dataclass(frozen=True)
class PeerFindingIndex:
    """Index of every reviewer finding in the session."""

    by_key: Mapping[str, PeerFindingRecord]
    all_records: tuple[PeerFindingRecord, ...]


def _canonical_id(agent_name: str, configuration: Configuration) -> str:
    return try_resolve_canonical_id(agent_name, configuration) or agent_name.lower()


def build_index_key(source_agent: str, finding_id: str) -> str:
    """Composite lookup key — ``{canonical}::{finding_id}``."""
    return f"{_canonical_id(source_agent, buddies_configuration())}::{finding_id}"


def _finding_producing_agents(configuration: Configuration) -> frozenset[str]:
    """Canonical ids of the LLM nodes — the only agents that can originate a finding."""
    return frozenset(
        _canonical_id(entry.key, configuration)
        for entry in configuration.entries
        if getattr(entry, "is_llm", False)
    )


def build_peer_finding_index(
    session_results: Mapping[str, Mapping[str, object]],
) -> PeerFindingIndex:
    """Build the index from a session's raw reviewer results.

    ``session_results`` maps ``agentName -> {"response": str}`` in insertion
    order (the ``raw_results.json`` shape).
    """
    by_key: dict[str, PeerFindingRecord] = {}
    all_records: list[PeerFindingRecord] = []
    configuration = buddies_configuration()
    producers = _finding_producing_agents(configuration)

    for agent_name, result in session_results.items():
        if is_judge_like_agent(agent_name, configuration):
            continue
        canonical = _canonical_id(agent_name, configuration)
        if canonical not in producers:
            continue
        response = response_of(result)
        if response is None:
            continue

        try:
            findings = extract_findings(agent_name, response, configuration)
        except Exception:
            continue

        for finding in findings:
            if not finding.id:
                continue
            key = f"{canonical}::{finding.id}"
            if key in by_key:
                # First-writer wins (duplicate ids across multi-pass agents).
                continue
            record = PeerFindingRecord(
                source_agent=agent_name,
                source_agent_canonical=canonical,
                finding_id=finding.id,
                finding=finding,
            )
            by_key[key] = record
            all_records.append(record)

    return PeerFindingIndex(by_key=by_key, all_records=tuple(all_records))
