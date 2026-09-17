"""specialist_findings: map a specialist-finding index onto neutral consolidation records.

The generic ``consolidate`` reducer (:mod:`roundtable.consolidation`) groups neutral
:class:`~roundtable.consolidation.core.Record`s; this module owns only the *domain
mapping* — what an InspectorX record IS: a finding groups by ``file``, positions along
``line``, carries ``severity``/``category`` as inline detail, and folds its grounding
fields into the appendix-only ``details`` tier. The grouping threshold and render
vocabulary are declared per node in ``agent_graph.yaml``'s ``consolidation:`` block, not
here. Registered as the ``specialist_findings`` extract seam by :mod:`.context_plugins`.

The 1-D ``line`` axis is deliberate: interval-overlap / semantic / enclosing-symbol keys
were prototyped and rejected because they added complexity without improving the
observed grouping.
"""

from __future__ import annotations

from roundtable.consolidation import Record
from roundtable.extraction import CodeBlock, FindingItem

from .specialist_finding_index import SpecialistFindingIndex


def _summary(title: str | None, description: str) -> str:
    """First non-empty line of the finding's title (else description)."""
    lines = (title or description or "").strip().splitlines()
    return lines[0] if lines else ""


def _fix_text(fix: CodeBlock | str | None) -> str | None:
    """Flatten the canonical ``fix`` union (code block or prose) to one string."""
    if isinstance(fix, CodeBlock):
        return fix.code.strip() or None
    if isinstance(fix, str) and fix.strip():
        return fix.strip()
    return None


def _details(f: FindingItem) -> tuple[tuple[str, str], ...]:
    """The appendix-only rich fields — everything the one-line summary can't carry.

    These are the grounding a Judge/SeverityInflator drills into: the full description,
    impact and exploitability, the supporting evidence, the execution trace, and the
    proposed fix. Only present fields are emitted, in a fixed reading order.
    """
    out: list[tuple[str, str]] = []
    description = (f.description or "").strip()
    if description:
        out.append(("description", description))
    if f.impact:
        out.append(("impact", f.impact.strip()))
    if f.exploitability is not None:
        rating = f.exploitability.rating.strip()
        reasoning = (f.exploitability.reasoning or "").strip()
        out.append(("exploitability", f"{rating} — {reasoning}" if reasoning else rating))
    if f.evidence:
        out.append(("evidence", "; ".join(f.evidence)))
    if f.trace:
        out.append(("trace", " → ".join(f.trace)))
    fix = _fix_text(f.fix)
    if fix:
        out.append(("fix", fix))
    return tuple(out)


def findings_to_records(index: SpecialistFindingIndex) -> list[Record]:
    """Map each indexed specialist finding onto a neutral consolidation record.

    ``file`` is the group key, ``line`` the positional axis; ``severity``/``category``
    become the inline detail and the concern's tag set, while the grounding fields
    (description, impact, exploitability, evidence, trace, fix) become the appendix-only
    ``details`` tier for a full-record drill-down.
    """
    records: list[Record] = []
    for r in index.all_records:
        f = r.finding
        records.append(
            Record(
                id=r.finding_id,
                source=r.source_agent_canonical,
                group_key=f.file,
                position=f.line,
                summary=_summary(f.title, f.description),
                attrs=(("severity", f.severity), ("category", f.category)),
                tags=(f.category,),
                details=_details(f),
            )
        )
    return records
