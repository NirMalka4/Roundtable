"""consolidation.core: the neutral Record, the grouping algorithm, and rendering.

This module is domain-agnostic — it never imports a finding, an agent, or a schema.
A caller adapts its own items into :class:`Record`s, calls :func:`build_consolidation`
to cluster + conserve them, and :func:`render_consolidation` with a :class:`RenderSpec`
to produce the delivered body. Roundtable's dossier is one such caller.

Two invariants make a consolidation a trustworthy zero-drop artifact:

  * **Determinism.** Records are sorted *before* grouping, so group membership depends
    only on the record set, never on scheduler/collection order.
  * **Conservation.** Every record lands in exactly one group — a true multiset
    partition, asserted on the unique ``source::id`` key (a bare count check would pass
    even if one record were duplicated and another dropped). So the rendered checksum
    total is the true in-scope record count (nothing is grouped away).

Grouping technique. **Gap-based single-linkage agglomerative clustering** on a
one-dimensional positional axis (:attr:`Record.position`), applied per group key with a
fixed :paramref:`adjacency_gap`: sort the anchors, then cut wherever the gap to the
previous anchor exceeds the threshold. Records with no :attr:`Record.group_key` become
deterministic singletons (they cannot be co-located). The elementary 1-D form is
deliberate; richer keys (interval-overlap, embedding/semantic, enclosing-symbol) were
prototyped and rejected because they added complexity without improving the observed
grouping. Placing the checksum at the tail keeps the count where recall is highest —
the head/tail of the window, not the lossy middle (Liu et al., "Lost in the Middle",
TACL 2024, arXiv:2307.03172).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Record:
    """One neutral fan-in record — the unit a consolidation groups and conserves.

    ``id`` is unique within ``source``; together they form the conservation key. A
    ``group_key`` of ``None`` marks a record that cannot be co-located (a deterministic
    singleton). ``position`` is the 1-D axis for gap-based clustering within a group key
    (``None`` ⇒ no positional adjacency). ``summary``/``attrs``/``tags`` are render-only:
    a one-line summary, ordered inline ``key=value`` detail, and group-header tag
    contributors respectively. ``details`` is the **appendix-only** tier — ordered
    ``key: value`` fields too long or numerous for the inline index (a full description,
    evidence, a fix, a trace…), rendered only when a ``RenderSpec`` asks for the
    ``index+appendix`` drill-down. Splitting ``attrs`` (compact, always inline) from
    ``details`` (rich, appendix-only) keeps the index scannable while the drill-down
    stays lossless.
    """

    id: str
    source: str
    group_key: str | None = None
    position: int | None = None
    summary: str = ""
    attrs: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()
    details: tuple[tuple[str, str], ...] = ()

    @property
    def key(self) -> str:
        """The unique conservation identity — ``source::id``."""
        return f"{self.source}::{self.id}"


@dataclass(frozen=True)
class Group:
    """A cohesive cluster of records (the consolidation's unit)."""

    group_key: str | None
    anchor: int | None
    members: tuple[Record, ...]

    @property
    def tags(self) -> tuple[str, ...]:
        """Distinct member tags, in first-seen order."""
        seen: dict[str, None] = {}
        for m in self.members:
            for t in m.tags:
                seen.setdefault(t, None)
        return tuple(seen)


@dataclass(frozen=True)
class Consolidation:
    """Grouped records + the zero-drop count checksum."""

    groups: tuple[Group, ...]
    item_count: int  # == total input records (zero-drop source of truth)

    @property
    def group_count(self) -> int:
        return len(self.groups)


@dataclass(frozen=True)
class RenderSpec:
    """The caller's vocabulary for :func:`render_consolidation`.

    Every string a domain wants in its delivered body lives here, so the core render is
    pure structure. Defaults cover the checksum header and the no-group anchor label.

    ``depth`` selects how much of each record is delivered. ``"index"`` (default) emits
    only the group-organized index — the compact, byte-stable form. ``"index+appendix"``
    additionally emits an *addressable appendix*: each index member line carries a back
    reference (``↳ #source::id``) to a tail ``#### source::id`` block that lays every
    emitted field out in full, so a consumer can drill from the summary into the complete
    record without a lossy re-summarization. The appendix sits at the tail where recall is
    highest (Liu et al., "Lost in the Middle", TACL 2024).
    """

    preamble: str
    empty_note: str
    group_prefix: str  # e.g. "C" -> "### C-1: ..."
    item_singular: str  # e.g. "finding" -> "| 2 finding(s)"
    group_plural_label: str  # e.g. "Concerns" -> checksum line
    item_plural_label: str  # e.g. "Findings" -> checksum line
    checksum_header: str = "Count Checksum"
    no_group_label: str = "(no location)"
    depth: str = "index"  # "index" | "index+appendix"
    appendix_header: str = "Details"


def _sort_key(record: Record) -> tuple[str, int, str, str]:
    """Total order over records — anchor first, then source/id for a stable tie-break."""
    return (
        record.group_key or "",
        record.position if record.position is not None else -1,
        record.source,
        record.id,
    )


def build_consolidation(records: list[Record], *, adjacency_gap: int) -> Consolidation:
    """Cluster records into groups (deterministic) and assert the zero-drop partition.

    Records sharing a ``group_key`` merge while adjacent (within ``adjacency_gap`` of the
    previous anchor, single-linkage); otherwise a new group starts. Records with no
    ``group_key`` are deterministic singletons. The record total is preserved exactly
    (multiset partition, asserted); grouping only affects presentation.
    """
    ordered = sorted(records, key=_sort_key)
    groups: list[Group] = []
    bucket: list[Record] = []
    bucket_key: str | None = None
    bucket_last_pos: int | None = None  # most recent anchor (adjacency origin)

    def _flush() -> None:
        if bucket:
            groups.append(
                Group(group_key=bucket_key, anchor=bucket[0].position, members=tuple(bucket))
            )

    for record in ordered:
        # No group key ⇒ deterministic singleton (cannot be co-located).
        if record.group_key is None:
            _flush()
            bucket, bucket_key = [], None
            bucket_last_pos = None
            groups.append(Group(group_key=None, anchor=None, members=(record,)))
            continue
        same_group = record.group_key == bucket_key
        # Adjacency: within adjacency_gap of the previous anchor (single-linkage step).
        close = (
            bucket_last_pos is not None
            and record.position is not None
            and abs(record.position - bucket_last_pos) <= adjacency_gap
        )
        if bucket and same_group and (record.position is None or bucket_last_pos is None or close):
            bucket.append(record)
        else:
            _flush()
            bucket = [record]
            bucket_key = record.group_key
        if record.position is not None:
            bucket_last_pos = record.position

    _flush()
    consolidation = Consolidation(groups=tuple(groups), item_count=len(records))
    _assert_partition(consolidation, records)
    return consolidation


def _assert_partition(consolidation: Consolidation, records: list[Record]) -> None:
    """Prove every record lands in exactly one group — a true multiset partition.

    A count check alone (``sum(len) == N``) would pass even if one record were duplicated
    while another was dropped; asserting multiset equality on the unique ``source::id``
    key rules that out, and no group may be empty. Holds by construction — a load-bearing
    guard on the zero-drop contract, not a fallible check.
    """
    grouped = Counter(m.key for g in consolidation.groups for m in g.members)
    expected = Counter(r.key for r in records)
    if grouped != expected or any(not g.members for g in consolidation.groups):
        raise AssertionError("consolidation partition violated (zero-drop contract)")


def _anchor(group: Group, spec: RenderSpec) -> str:
    if group.group_key is None:
        return spec.no_group_label
    if group.anchor is None:
        return group.group_key
    return f"{group.group_key}:{group.anchor}"


_INDEX_DEPTH = "index"
_APPENDIX_DEPTH = "index+appendix"
_DEPTHS = frozenset({_INDEX_DEPTH, _APPENDIX_DEPTH})


def _member_line(record: Record, *, with_ref: bool) -> str:
    attrs = f" ({', '.join(f'{k}={v}' for k, v in record.attrs)})" if record.attrs else ""
    tail = f" — {record.summary}" if record.summary else ""
    ref = f"  ↳ #{record.key}" if with_ref else ""
    return f"- {record.key}{attrs}{tail}{ref}"


def _appendix_block(record: Record) -> list[str]:
    """The record's full, addressable tail block — one emitted field per line.

    Shows the compact ``attrs`` (so the block is self-contained when reached via a back
    reference) followed by the appendix-only ``details`` (the fields too rich for the
    inline index), then the tag set.
    """
    lines = [f"#### {record.key}"]
    if record.summary:
        lines.append(record.summary)
    lines.extend(f"{k}: {v}" for k, v in record.attrs)
    lines.extend(f"{k}: {v}" for k, v in record.details)
    if record.tags:
        lines.append(f"tags: {', '.join(record.tags)}")
    return lines


def _assert_addressable(full: set[str], index_keys: set[str], appendix_keys: set[str]) -> None:
    """Prove the render is addressable zero-drop: index ids == appendix ids == records.

    A count-only checksum can pass while a record is silently dropped from (or duplicated
    in) the delivered text. Asserting the index — and, when present, the appendix — cover
    exactly the conserved key set makes drill-down total: every summarized record resolves
    to its full block, and nothing is presented that was not consolidated.
    """
    if index_keys != full or (appendix_keys and appendix_keys != full):
        raise AssertionError("consolidation render not addressable (zero-drop contract)")


def render_consolidation(consolidation: Consolidation, spec: RenderSpec) -> str:
    """Render the consolidation body (no delivery heading — delivery adds it).

    Emits the referenceable, group-organized index (member lines carry the canonical
    ``source::id`` key plus inline attrs and a short summary) followed by the
    ``### <checksum_header>`` footer whose item total is the zero-drop source of truth.
    When ``spec.depth == "index+appendix"`` a tail appendix of full ``#### source::id``
    blocks follows, and each index line back-references its block — the render is then
    asserted *addressable* zero-drop (index ids == appendix ids == input records).
    """
    if spec.depth not in _DEPTHS:
        raise ValueError(f"unknown render depth {spec.depth!r} (expected one of {sorted(_DEPTHS)})")
    with_appendix = spec.depth == _APPENDIX_DEPTH
    full = {m.key for g in consolidation.groups for m in g.members}
    lines: list[str] = [spec.preamble, ""]
    if not consolidation.groups:
        lines.append(spec.empty_note)
    index_keys: set[str] = set()
    for i, group in enumerate(consolidation.groups, start=1):
        tags = ", ".join(group.tags)
        n = len(group.members)
        lines.append(
            f"### {spec.group_prefix}-{i}: {_anchor(group, spec)} | {n} {spec.item_singular}(s) [{tags}]"
        )
        for m in group.members:
            lines.append(_member_line(m, with_ref=with_appendix))
            index_keys.add(m.key)
        lines.append("")
    lines.append(f"### {spec.checksum_header}")
    lines.append(f"{spec.group_plural_label}: {consolidation.group_count}")
    lines.append(f"{spec.item_plural_label}: {consolidation.item_count}")
    appendix_keys: set[str] = set()
    if with_appendix:
        lines.extend(["", f"### {spec.appendix_header}"])
        for group in consolidation.groups:
            for m in group.members:
                lines.extend(_appendix_block(m))
                lines.append("")
                appendix_keys.add(m.key)
    _assert_addressable(full, index_keys, appendix_keys)
    return "\n".join(lines)
