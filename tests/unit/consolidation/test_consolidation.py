"""Unit tests for the neutral consolidation core.

Guards the invariants that make a consolidation a trustworthy zero-drop artifact,
independent of any domain:

  * **Conservation** — every record lands in exactly one group (multiset partition); the
    checksum item total equals the input record count.
  * **Determinism** — records are sorted before grouping, so membership depends only on
    the record set, never on input order.
  * **Grouping** — gap-based single-linkage on ``position`` within a ``group_key``.
"""

from __future__ import annotations

from collections import Counter

from roundtable.consolidation import (
    Record,
    RenderSpec,
    build_consolidation,
    render_consolidation,
)

_GAP = 10

_SPEC = RenderSpec(
    preamble="PRE",
    empty_note="_none_",
    group_prefix="G",
    item_singular="item",
    group_plural_label="Groups",
    item_plural_label="Items",
)


def _rec(source: str, rid: str, *, group=None, position=None, summary="", cat="c") -> Record:
    return Record(
        id=rid,
        source=source,
        group_key=group,
        position=position,
        summary=summary,
        attrs=(("severity", "medium"), ("category", cat)),
        tags=(cat,),
    )


def test_checksum_conservation_every_record_lands_once():
    records = [
        _rec("Security", "S1", group="a.py", position=10),
        _rec("Logic", "L1", group="a.py", position=12),  # within gap -> same group
        _rec("Docs", "D1", group="b.py", position=1),
        _rec("Privacy", "P1"),  # groupless singleton
    ]
    c = build_consolidation(records, adjacency_gap=_GAP)
    assert c.item_count == 4
    assert sum(len(g.members) for g in c.groups) == 4


def test_close_records_merge_far_records_split():
    near = [
        _rec("A", "1", group="f.py", position=10),
        _rec("B", "2", group="f.py", position=10 + _GAP),  # exactly at the gap -> merges
    ]
    assert build_consolidation(near, adjacency_gap=_GAP).group_count == 1

    far = [
        _rec("A", "1", group="f.py", position=10),
        _rec("B", "2", group="f.py", position=10 + _GAP + 1),  # beyond the gap -> splits
    ]
    assert build_consolidation(far, adjacency_gap=_GAP).group_count == 2


def test_partition_is_multiset_exact():
    records = [
        _rec("Security", "S1", group="a.py", position=10),
        _rec("Logic", "L1", group="a.py", position=200),
        _rec("Docs", "D1", group="b.py", position=5),
        _rec("Privacy", "P1"),
    ]
    c = build_consolidation(records, adjacency_gap=_GAP)
    seen = Counter(m.key for g in c.groups for m in g.members)
    expected = Counter(r.key for r in records)
    assert seen == expected
    assert all(len(g.members) >= 1 for g in c.groups)


def test_different_group_keys_never_merge():
    records = [
        _rec("A", "1", group="a.py", position=10),
        _rec("B", "2", group="b.py", position=10),
    ]
    assert build_consolidation(records, adjacency_gap=_GAP).group_count == 2


def test_groupless_records_are_singletons():
    records = [_rec("A", "1"), _rec("B", "2")]
    c = build_consolidation(records, adjacency_gap=_GAP)
    assert c.group_count == 2
    assert all(len(g.members) == 1 for g in c.groups)


def test_grouping_is_order_independent():
    records = [
        _rec("Security", "S1", group="a.py", position=10),
        _rec("Logic", "L1", group="a.py", position=12),
        _rec("Docs", "D1", group="b.py", position=5),
    ]
    forward = build_consolidation(records, adjacency_gap=_GAP)
    backward = build_consolidation(list(reversed(records)), adjacency_gap=_GAP)

    def shape(c):
        return [(g.group_key, g.anchor, [m.id for m in g.members]) for g in c.groups]

    assert shape(forward) == shape(backward)


def test_render_emits_keys_attrs_and_checksum():
    records = [
        _rec("Security", "S1", group="a.py", position=10, summary="boom", cat="security"),
        _rec("Logic", "L1", group="a.py", position=12, summary="oops"),
    ]
    body = render_consolidation(build_consolidation(records, adjacency_gap=_GAP), _SPEC)
    assert "Security::S1" in body
    assert "Logic::L1" in body
    assert "(severity=medium, category=security)" in body
    assert "— boom" in body
    assert "### Count Checksum" in body
    assert "Items: 2" in body
    assert "Groups: 1" in body
    assert "### G-1: a.py:10 | 2 item(s) [security, c]" in body


def test_render_empty_consolidation():
    body = render_consolidation(build_consolidation([], adjacency_gap=_GAP), _SPEC)
    assert "_none_" in body
    assert "Items: 0" in body


def test_groupless_anchor_uses_no_group_label():
    body = render_consolidation(build_consolidation([_rec("A", "1")], adjacency_gap=_GAP), _SPEC)
    assert "### G-1: (no location) | 1 item(s)" in body


_APPENDIX_SPEC = RenderSpec(
    preamble="PRE",
    empty_note="_none_",
    group_prefix="G",
    item_singular="item",
    group_plural_label="Groups",
    item_plural_label="Items",
    depth="index+appendix",
)


def test_index_depth_is_byte_identical_default():
    """Default depth adds nothing over the index — no appendix, no back-references."""
    records = [_rec("Security", "S1", group="a.py", position=10, summary="boom")]
    c = build_consolidation(records, adjacency_gap=_GAP)
    body = render_consolidation(c, _SPEC)
    assert "↳" not in body
    assert "### Details" not in body


def test_appendix_depth_emits_full_addressable_blocks():
    records = [
        _rec("Security", "S1", group="a.py", position=10, summary="boom", cat="security"),
        _rec("Logic", "L1", group="a.py", position=12, summary="oops"),
    ]
    c = build_consolidation(records, adjacency_gap=_GAP)
    body = render_consolidation(c, _APPENDIX_SPEC)
    # Index member lines back-reference their appendix block.
    assert "- Security::S1 (severity=medium, category=security) — boom  ↳ #Security::S1" in body
    # A tail appendix section lays every emitted field out in full, one per line.
    assert "### Details" in body
    assert "#### Security::S1" in body
    assert "#### Logic::L1" in body
    assert "severity: medium" in body
    assert "category: security" in body
    assert "tags: security" in body
    # Appendix sits after the checksum (tail placement for recall).
    assert body.index("### Count Checksum") < body.index("### Details")


def test_appendix_is_addressable_every_index_key_resolves():
    records = [
        _rec("Security", "S1", group="a.py", position=10),
        _rec("Logic", "L1", group="b.py", position=200),
        _rec("Privacy", "P1"),  # groupless singleton
    ]
    c = build_consolidation(records, adjacency_gap=_GAP)
    body = render_consolidation(c, _APPENDIX_SPEC)
    for key in ("Security::S1", "Logic::L1", "Privacy::P1"):
        assert f"- {key}" in body  # index line
        assert f"#### {key}" in body  # appendix block
        assert f"↳ #{key}" in body  # back-reference


def test_appendix_renders_details_tier_not_in_index():
    """`details` are appendix-only — the compact index never carries them inline."""
    rec = Record(
        id="1",
        source="A",
        group_key="f.py",
        position=10,
        summary="short line",
        attrs=(("severity", "high"),),
        details=(("description", "the full multi-field grounding"), ("fix", "guard the call")),
        tags=("security",),
    )
    body = render_consolidation(build_consolidation([rec], adjacency_gap=_GAP), _APPENDIX_SPEC)
    index, _, appendix = body.partition("### Details")
    # The rich detail lives only in the appendix, never inline in the index.
    assert "the full multi-field grounding" not in index
    assert "description: the full multi-field grounding" in appendix
    assert "fix: guard the call" in appendix
    # The compact attr still shows inline in the index.
    assert "(severity=high)" in index


def test_unknown_depth_is_rejected():
    import pytest

    bad = RenderSpec(
        preamble="P",
        empty_note="_",
        group_prefix="G",
        item_singular="item",
        group_plural_label="Groups",
        item_plural_label="Items",
        depth="index+full",
    )
    with pytest.raises(ValueError, match="unknown render depth"):
        render_consolidation(build_consolidation([_rec("A", "1")], adjacency_gap=_GAP), bad)
