"""peer_findings: map a peer-finding index onto neutral consolidation records.

The generic ``consolidate`` reducer (:mod:`roundtable.consolidation`) groups neutral
:class:`~roundtable.consolidation.core.Record`s; this module owns only the *domain
mapping* — what a reviewer record IS: a finding groups by ``file``, positions along
``line``, carries ``severity`` as inline detail, and tags its source reviewer. The
grouping threshold and render vocabulary are declared per node in ``agent_graph.yaml``'s
``consolidation:`` block, not here. Registered as the ``peer_findings`` extract seam by
:mod:`.context_plugins`.

The buddies dossier is rendered **index-only** (``depth: index``): a scannable map of
every finding keyed by its canonical ``agent::finding_id`` with severity + one-line
summary. The *full* per-finding detail is not re-projected here — the Judge receives each
reviewer's raw output verbatim through its direct edges and drills into that, so this
mapping deliberately carries no appendix ``details`` tier.

The 1-D ``line`` axis is deliberate: interval-overlap / semantic / enclosing-symbol keys
were prototyped and rejected because they added complexity without improving the
observed grouping.
"""

from __future__ import annotations

from roundtable.consolidation import Record
from roundtable.extraction import FindingItem

from .peer_finding_index import PeerFindingIndex


def _summary(title: str | None, description: str) -> str:
    """First non-empty line of the finding's title (else description)."""
    lines = (title or description or "").strip().splitlines()
    return lines[0] if lines else ""


def findings_to_records(index: PeerFindingIndex) -> list[Record]:
    """Map each indexed reviewer finding onto a neutral consolidation record.

    ``file`` is the group key, ``line`` the positional axis; ``severity`` is the inline
    detail and the producing reviewer is the concern's tag (buddies has no per-finding
    ``category`` — the discipline IS the source reviewer, so the group header aggregates
    *which reviewers* flag a file from real provenance, not a fabricated constant). No
    appendix ``details`` are attached: the index is a map, and the Judge drills into each
    reviewer's raw output for the full finding.
    """
    records: list[Record] = []
    for r in index.all_records:
        f: FindingItem = r.finding
        records.append(
            Record(
                id=r.finding_id,
                source=r.source_agent_canonical,
                group_key=f.file,
                position=f.line,
                summary=_summary(f.title, f.description),
                attrs=(("severity", f.severity),),
                tags=(r.source_agent_canonical,),
            )
        )
    return records
