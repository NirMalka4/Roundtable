"""Unit tests for the review-domain overlay of trace.json."""

from __future__ import annotations

from roundtable.decision.verdict import VerdictResult, render_verdict_md
from roundtable.review.trace_overlay import OverlayKey, build_overlay


def _verdict(v: str = "REJECT", overridden: bool = False) -> VerdictResult:
    return VerdictResult(
        verdict=v,
        verdict_icon="\u274c",
        verdict_overridden=overridden,
        reason=f"Review verdict: {v}.",
        session_id="sess_x",
    )


def test_build_overlay_always_carries_verdict_surface_and_counts_slot() -> None:
    overlay = build_overlay(_verdict("APPROVE"))
    assert overlay[OverlayKey.VERDICT] == "APPROVE"
    assert overlay[OverlayKey.VERDICT_ICON] == "\u274c"
    assert overlay[OverlayKey.VERDICT_OVERRIDDEN] is False
    # counts slot is always present (None when no count block), matching the legacy record.
    assert overlay[OverlayKey.COUNTS] is None


def test_build_overlay_records_counts_when_supplied() -> None:
    overlay = build_overlay(
        _verdict("REJECT"), counts={"blocking": 1, "nonBlocking": 0, "all": 1, "security": 0}
    )
    assert overlay[OverlayKey.COUNTS]["blocking"] == 1


def test_build_overlay_records_subject_diff_provenance_when_supplied() -> None:
    subject = {"mode": "pr", "repo": "MyRepo", "prId": 42, "sourceSha": "abc123"}
    diff_stat = {"filesChanged": 2, "insertions": 10, "deletions": 3}
    provenance = {"toolName": "roundtable", "toolVersion": "0.0.0"}
    overlay = build_overlay(
        _verdict(),
        subject=subject,
        diff_stat=diff_stat,
        provenance=provenance,
    )
    assert overlay[OverlayKey.SUBJECT] == subject
    assert overlay[OverlayKey.DIFF_STAT] == diff_stat
    assert overlay[OverlayKey.PROVENANCE] == provenance


def test_build_overlay_omits_optional_blocks_when_absent() -> None:
    overlay = build_overlay(_verdict())
    # Byte-stable: additive blocks are absent unless supplied.
    assert OverlayKey.SUBJECT not in overlay
    assert OverlayKey.DIFF_STAT not in overlay
    assert OverlayKey.PROVENANCE not in overlay


def test_render_verdict_md_notes_override() -> None:
    md = render_verdict_md(_verdict("REJECT", overridden=True))
    assert "auto-overridden to REJECT" in md


def test_render_verdict_md_renders_counts_when_supplied() -> None:
    md = render_verdict_md(
        _verdict("REJECT"), {"blocking": 1, "nonBlocking": 2, "all": 3, "security": 0}
    )
    assert "REJECT" in md
    assert "Blocking: 1" in md
    assert "Non-blocking: 2" in md
    assert "Total: 3" in md
