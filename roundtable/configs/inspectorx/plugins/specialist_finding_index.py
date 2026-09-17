"""specialist_finding_index: canonical index of specialist findings.

Builds a queryable
index of every specialist finding in the session, keyed by
``{canonical_agent_id}::{finding_id}``, so Judge's verdict-overlay entries can be
resolved back to the originating specialist finding record (locations, suggestions,
evidence) at publish time.

Skips Judge-like agents (Judge cannot reference its own output). First-writer
wins on duplicate composite keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from roundtable.extraction import FindingItem, extract_findings
from roundtable.result_access import response_of
from roundtable.runtime import is_judge_like_agent, try_resolve_canonical_id

from .configuration import inspectorx_configuration


@dataclass(frozen=True)
class SpecialistFindingRecord:
    """A single specialist finding indexed for overlay resolution."""

    source_agent: str
    source_agent_canonical: str
    finding_id: str
    finding: FindingItem


@dataclass(frozen=True)
class SpecialistFindingIndex:
    """Index of every specialist finding in the session."""

    by_key: Mapping[str, SpecialistFindingRecord]
    all_records: tuple[SpecialistFindingRecord, ...]


def build_index_key(source_agent: str, finding_id: str) -> str:
    """Composite lookup key — ``{canonical}::{finding_id}``."""
    canonical = (
        try_resolve_canonical_id(source_agent, inspectorx_configuration()) or source_agent.lower()
    )
    return f"{canonical}::{finding_id}"


def build_specialist_finding_index(
    session_results: Mapping[str, Mapping[str, object]],
) -> SpecialistFindingIndex:
    """Build the index from a session's raw specialist results.

    ``session_results`` maps ``agentName -> {"response": str}`` in insertion
    order (the ``raw_results.json`` shape).
    """
    by_key: dict[str, SpecialistFindingRecord] = {}
    all_records: list[SpecialistFindingRecord] = []

    for agent_name, result in session_results.items():
        configuration = inspectorx_configuration()
        if is_judge_like_agent(agent_name, configuration):
            continue
        response = response_of(result)
        if response is None:
            continue

        canonical = try_resolve_canonical_id(agent_name, configuration) or agent_name.lower()

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
            record = SpecialistFindingRecord(
                source_agent=agent_name,
                source_agent_canonical=canonical,
                finding_id=finding.id,
                finding=finding,
            )
            by_key[key] = record
            all_records.append(record)

    return SpecialistFindingIndex(by_key=by_key, all_records=tuple(all_records))
