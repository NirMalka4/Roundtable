"""Unit tests for the buddies consolidation seam: ``peer_findings``.

These guard two faithfulness (E-FAITH) properties of the **index-only** buddies dossier
(``depth: index``):

1. **No fabricated ``category``.** No buddies reviewer schema declares a ``category``
   field, so the extractor's ``default_category`` used to bleed a constant
   ``category=unknown`` into every dossier row. Buddies' discipline *is* the source
   reviewer, so the mapping drops ``category`` entirely and tags each concern with its
   real source agent instead.
2. **Index carries the canonical id + severity + summary, nothing more.** The dossier is
   a scannable map keyed by ``agent::finding_id``; the *full* finding detail is delivered
   to the Judge raw through its direct per-reviewer edges, not re-projected here. So the
   mapping attaches no appendix ``details`` tier and the render emits no ``### Details``
   section.
"""

from __future__ import annotations

import pytest

from roundtable.bundle.paths import set_config_root
from roundtable.configs.buddies.plugins.peer_finding_index import (
    PeerFindingIndex,
    PeerFindingRecord,
)
from roundtable.configs.buddies.plugins.peer_findings import findings_to_records
from roundtable.consolidation import build_consolidation, render_consolidation
from roundtable.extraction.finding_extractor import FindingItem
from roundtable.graph.loader import load_agent_graph
from roundtable.graph.model import ConsolidationSpec

_BUDDIES_ROOT = "roundtable/configs/buddies"


@pytest.fixture(autouse=True)
def _use_buddies_config():
    """Point the config seam at the buddies bundle for the duration of each test."""
    set_config_root(_BUDDIES_ROOT)
    try:
        yield
    finally:
        set_config_root(None)


def _dossier_spec() -> ConsolidationSpec:
    """The real declarative consolidation spec of the buddies Consolidation node."""
    graph = {e.key: e for e in load_agent_graph()}
    spec = graph["Consolidation"].consolidation
    assert spec is not None
    return spec


def _rec(
    agent: str, fid: str, *, file=None, line=None, severity="medium", title="t", description=""
) -> PeerFindingRecord:
    finding = FindingItem(
        agent_name=agent,
        id=fid,
        severity=severity,
        category="unknown",  # what the extractor default would produce; must NOT surface
        file=file,
        line=line,
        title=title,
        description=description,
    )
    return PeerFindingRecord(
        source_agent=agent,
        source_agent_canonical=agent.lower(),
        finding_id=fid,
        finding=finding,
    )


def _index(records: list[PeerFindingRecord]) -> PeerFindingIndex:
    by_key = {f"{r.source_agent_canonical}::{r.finding_id}": r for r in records}
    return PeerFindingIndex(by_key=by_key, all_records=tuple(records))


def _body(records: list[PeerFindingRecord]) -> str:
    spec = _dossier_spec()
    consolidation = build_consolidation(
        findings_to_records(_index(records)), adjacency_gap=spec.adjacency_gap
    )
    return render_consolidation(consolidation, spec.render)


def test_node_declares_peer_findings_seam_index_only():
    spec = _dossier_spec()
    assert spec.extract == "peer_findings"
    assert spec.render.depth == "index"


def test_mapping_drops_category_and_tags_source_agent():
    [record] = findings_to_records(_index([_rec("taintcheck", "TC-9", file="a.ts", line=10)]))
    assert record.attrs == (("severity", "medium"),)  # no ("category", …)
    assert record.tags == ("taintcheck",)  # real provenance, not a fabricated constant


def test_mapping_attaches_no_appendix_details():
    [record] = findings_to_records(_index([_rec("taintcheck", "TC-9", file="a.ts", line=10)]))
    assert record.details == ()  # detail comes from the reviewer's raw output, not here


def test_category_unknown_never_reaches_rendered_dossier():
    body = _body(
        [
            _rec("taintcheck", "TC-9", file="a.ts", line=10),
            _rec("north_star", "NS-1", title="dependency drift"),
        ]
    )
    assert "category" not in body
    assert "unknown" not in body


def test_render_is_index_only_no_details_section():
    body = _body([_rec("taintcheck", "TC-9", file="a.ts", line=10, title="tainted sink")])
    assert "### Details" not in body  # appendix tier is gone


def test_index_line_carries_canonical_id_severity_and_summary():
    body = _body(
        [_rec("taintcheck", "TC-9", file="a.ts", line=10, severity="high", title="tainted sink")]
    )
    assert "taintcheck::TC-9" in body  # canonical id addressable by the Judge / coverage gate
    assert "severity=high" in body
    assert "tainted sink" in body
