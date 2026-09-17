"""Unit tests for the Roundtable consolidation seam: the finding→record mapping
and the render vocabulary declared on the migrated ``Dossier_*`` nodes.

The zero-drop invariants (conservation, determinism, gap clustering) are proven
domain-neutrally in ``tests/unit/consolidation/test_consolidation.py``. These tests
guard the Roundtable-specific half: the ``findings_to_records`` mapping and that the
declarative ``consolidation:`` block on the real graph still renders the vocabulary
(``Concerns:`` / ``Findings:`` / ``### Count Checksum`` / ``agent::finding_id``) the
Judge and SeverityInflator prompts reconcile against.
"""

from __future__ import annotations

from roundtable.bundle import resolve_bundle
from roundtable.configs.inspectorx.plugins.specialist_finding_index import (
    SpecialistFindingIndex,
    SpecialistFindingRecord,
)
from roundtable.configs.inspectorx.plugins.specialist_findings import findings_to_records
from roundtable.consolidation import build_consolidation, render_consolidation
from roundtable.extraction.finding_extractor import CodeBlock, Exploitability, FindingItem
from roundtable.graph.model import ConsolidationSpec, get_configuration

_CONFIG = get_configuration(resolve_bundle("inspectorx"))


def _dossier_spec() -> ConsolidationSpec:
    """The real declarative consolidation spec of a migrated Dossier node."""
    graph = _CONFIG.by_key
    spec = graph["Dossier_Judge"].consolidation
    assert spec is not None
    return spec


def _body(records: list[SpecialistFindingRecord]) -> str:
    """Render synthetic records through the real declared render spec + gap."""
    spec = _dossier_spec()
    consolidation = build_consolidation(
        findings_to_records(_index(records)), adjacency_gap=spec.adjacency_gap
    )
    return render_consolidation(consolidation, spec.render)


def _rec(
    agent: str,
    fid: str,
    *,
    file: str | None,
    line: int | None,
    severity="medium",
    category="logic",
    title="t",
    description="",
    impact="",
    evidence=(),
    trace=(),
    exploitability=None,
    fix=None,
) -> SpecialistFindingRecord:
    finding = FindingItem(
        agent_name=agent,
        id=fid,
        severity=severity,
        category=category,
        file=file,
        line=line,
        title=title,
        description=description,
        impact=impact,
        evidence=tuple(evidence),
        trace=tuple(trace),
        exploitability=exploitability,
        fix=fix,
    )
    return SpecialistFindingRecord(
        source_agent=agent,
        source_agent_canonical=agent.lower(),
        finding_id=fid,
        finding=finding,
    )


def _index(records: list[SpecialistFindingRecord]) -> SpecialistFindingIndex:
    by_key = {f"{r.source_agent_canonical}::{r.finding_id}": r for r in records}
    return SpecialistFindingIndex(by_key=by_key, all_records=tuple(records))


def test_migrated_node_declares_specialist_findings_seam():
    spec = _dossier_spec()
    assert spec.extract == "specialist_findings"
    assert spec.adjacency_gap == 10
    assert spec.render.group_plural_label == "Concerns"
    assert spec.render.item_plural_label == "Findings"


def test_mapping_uses_file_as_group_and_line_as_position():
    [record] = findings_to_records(
        _index([_rec("Security", "S1", file="a.py", line=10, severity="high", category="security")])
    )
    assert record.id == "S1"
    assert record.source == "security"
    assert record.group_key == "a.py"
    assert record.position == 10
    assert record.attrs == (("severity", "high"), ("category", "security"))
    assert record.tags == ("security",)
    assert record.summary == "t"


def test_mapping_summary_falls_back_to_description():
    finding = FindingItem(
        agent_name="A", id="1", severity="low", category="docs", title=None, description="desc line"
    )
    rec = SpecialistFindingRecord(
        source_agent="A", source_agent_canonical="a", finding_id="1", finding=finding
    )
    [record] = findings_to_records(_index([rec]))
    assert record.summary == "desc line"


def test_body_renders_roundtable_vocabulary_and_tuples():
    records = [
        _rec("Security", "S1", file="a.py", line=10, severity="high", category="security"),
        _rec("Logic", "L1", file="a.py", line=12),  # within adjacency_gap -> one concern
    ]
    body = _body(records)
    assert "security::S1" in body
    assert "logic::L1" in body
    assert "### Count Checksum" in body
    assert "Findings: 2" in body
    assert "Concerns: 1" in body
    assert "grouped by code location into concerns" in body


def test_body_conserves_all_findings_far_apart():
    records = [
        _rec("Security", "S1", file="a.py", line=10),
        _rec("Logic", "L1", file="a.py", line=21),  # beyond gap 10 -> 2 concerns
        _rec("Privacy", "P1", file=None, line=None),  # locationless singleton
    ]
    body = _body(records)
    assert "Findings: 3" in body
    assert "Concerns: 3" in body


def test_empty_dossier_body():
    body = _body([])
    assert "No specialist findings in scope" in body
    assert "Findings: 0" in body


def test_migrated_node_declares_appendix_drill_down_depth():
    """The live Dossier node is flipped to index+appendix so the Judge can drill in."""
    assert _dossier_spec().render.depth == "index+appendix"


def test_findings_to_records_folds_grounding_into_details():
    """Rich grounding fields land in the appendix-only ``details`` tier, in reading order."""
    [record] = findings_to_records(
        _index(
            [
                _rec(
                    "Security",
                    "S1",
                    file="a.py",
                    line=10,
                    description="full body",
                    impact="RCE",
                    evidence=("call at a.py:10", "sink at b.py:2"),
                    trace=("entry", "sink"),
                    exploitability=Exploitability(rating="high", reasoning="reachable"),
                    fix=CodeBlock(language="py", code="guard()"),
                )
            ]
        )
    )
    assert record.details == (
        ("description", "full body"),
        ("impact", "RCE"),
        ("exploitability", "high — reachable"),
        ("evidence", "call at a.py:10; sink at b.py:2"),
        ("trace", "entry → sink"),
        ("fix", "guard()"),
    )


def test_body_appendix_carries_full_grounding_without_breaking_checksum():
    """The appendix surfaces the drill-down fields; counts/checksum stay additive."""
    records = [
        _rec(
            "Security",
            "S1",
            file="a.py",
            line=10,
            description="the full description",
            impact="RCE",
            fix=CodeBlock(language="py", code="guard()"),
        )
    ]
    body = _body(records)
    index, _, appendix = body.partition("### Details")
    assert appendix  # depth flip actually emitted the tail appendix
    assert "#### security::S1" in appendix
    assert "description: the full description" in appendix
    assert "impact: RCE" in appendix
    assert "fix: guard()" in appendix
    # Rich fields are appendix-only — never inline in the compact index.
    assert "the full description" not in index
    # Zero-drop footer is unchanged (additive appendix).
    assert "Findings: 1" in body
    assert "Concerns: 1" in body
