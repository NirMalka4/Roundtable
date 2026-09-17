"""Unit tests for the human ``verdict.md`` report renderer (ado.report).

The live smoke surfaced a thin 95-byte ``verdict.md`` (slim stub) even though the
session carried 7 blocking + 29 non-blocking findings. These tests pin the rich
renderer: a verdict header, executive summary, per-section finding listings with
severity/location/source, and the needs-human-judgment / validated-safe blocks.
"""

from __future__ import annotations

from roundtable.ado.publish import PublishableFinding
from roundtable.configs.inspectorx.plugins.report_renderer import (
    judge_executive_summary,
    render_review_report,
)
from roundtable.configs.inspectorx.plugins.verdict_overlay import (
    OverlayRef,
    PublishPlan,
    PublishPlanDiagnostics,
)
from roundtable.decision.verdict import VerdictResult


def _finding(**kw) -> PublishableFinding:
    base = {
        "id": "F-001",
        "title": "A truncation bug",
        "description": "A truncation bug in the column width.",
        "severity": "High",
        "file_path": "db/sp.sql",
        "start_line": 119,
        "end_line": 119,
        "location_index": 0,
        "total_locations": 1,
        "additional_locations": (),
        "stable_hash": "abc",
        "category": "blocking",
        "judge_category": None,
        "source_agents": ("CodeCorrectness", "SeverityInflator"),
        "fix": "Widen the column to NVARCHAR(500).",
    }
    base.update(kw)
    return PublishableFinding(**base)


def _plan(**kw) -> PublishPlan:
    base = {
        "verdict": "REJECT",
        "verdict_icon": "\u274c",
        "session_id": "s",
        "safe_count": 0,
        "original_findings_count": 0,
        "blocking_findings": [],
        "non_blocking_findings": [],
        "all_findings": [],
        "security_findings": [],
        "published_primary_count": 0,
        "judge_observations": [],
        "validated_safe_refs": [],
        "needs_human_judgment_refs": [],
        "unresolved_refs": [],
        "diagnostics": PublishPlanDiagnostics(),
    }
    base.update(kw)
    return PublishPlan(**base)


def _verdict(**kw) -> VerdictResult:
    base = {
        "verdict": "REJECT",
        "verdict_icon": "\u274c",
        "verdict_overridden": False,
        "reason": "Review verdict: REJECT. 1 blocking, 1 non-blocking findings.",
        "session_id": "s",
    }
    base.update(kw)
    return VerdictResult(**base)


def test_report_lists_blocking_and_non_blocking_findings():
    plan = _plan(
        blocking_findings=[_finding(id="COR-001", judge_category="logic_error")],
        non_blocking_findings=[_finding(id="OPT-001", category="non_blocking", severity="Low")],
    )
    md = render_review_report(
        _verdict(),
        plan,
        repo_name="ExampleRepo",
        source_branch="users/private-user/topic",
        executive_summary="The big picture.",
    )

    # Metadata header
    assert "# 🕵️ InspectorX Deep Compute Review" in md
    assert "**Repository**: ExampleRepo  " in md
    assert "**Source Branch**: users/private-user/topic  " in md
    assert "**Review Date**: " in md
    # Executive summary + friendly verdict label
    assert "## 📊 Executive Summary" in md
    assert "**Verdict**: ❌ Reject" in md
    assert "### TL;DR" in md
    assert "The big picture." in md
    assert "### Why this matters" in md
    assert "### Watch items" in md
    assert "| Severity | Finding | File |" in md
    assert "**Count Verification**: ✅ Passed" in md
    # Blocking finding renders its own real severity (not a hardcoded CRITICAL)
    assert "## 🎯 Blocking Findings" in md
    assert "### COR-001: A truncation bug" in md
    assert "**Severity**: 🔶 high" in md
    assert "**Location**: db/sp.sql:119" in md
    assert "**Category**: logic_error" in md
    assert "**Agent**: Code Correctness, Severity Review" in md
    assert "#### Description" in md
    assert "A truncation bug in the column width." in md
    assert "**Fix**:" in md
    assert "Widen the column to NVARCHAR(500)." in md
    # Non-blocking finding renders its own severity
    assert "## ⚠️ Non-Blocking Issues" in md
    assert "### OPT-001: A truncation bug" in md
    assert "**Severity**: 🟦 low" in md


def test_report_renders_prose_fix_as_markdown_block():
    """A prose fix is contract-typed as Markdown, so its block structure must
    survive: flattening newlines to ``<br>`` destroyed lists, paragraphs and
    fenced blocks alike (the fenced case also leaked raw HTML, since the inline
    helper skips escaping inside backtick spans)."""
    fix = "Do this in two steps:\n\n1. Move the guard up.\n2. Delete the duplicate."
    md = render_review_report(_verdict(), _plan(blocking_findings=[_finding(fix=fix)]))
    assert "<br>" not in md
    assert "1. Move the guard up.\n2. Delete the duplicate." in md


def test_report_keeps_balanced_fence_in_prose_fix_verbatim():
    fix = "Extract a helper.\n\n```cs\nTask<T> Execute<T>(Func<Task<T>> op);\n```"
    md = render_review_report(_verdict(), _plan(blocking_findings=[_finding(fix=fix)]))
    assert "```cs\nTask<T> Execute<T>(Func<Task<T>> op);\n```" in md


def test_report_defangs_unbalanced_fence_in_prose_fix():
    """An unclosed fence would swallow the rest of the report."""
    md = render_review_report(
        _verdict(), _plan(blocking_findings=[_finding(fix="Try:\n\n```cs\nvar x = 1;")])
    )
    assert "```" not in md
    assert "## ⚠️ Non-Blocking Issues" in md


def test_report_renders_evidence_bullets():
    plan = _plan(
        blocking_findings=[_finding(id="COR-001", evidence=("Line 1: foo", "Line 2: bar"))],
    )
    md = render_review_report(_verdict(), plan)
    assert "#### Evidence" in md
    assert "- Line 1: foo" in md
    assert "- Line 2: bar" in md


def test_report_empty_sections_render_placeholders():
    md = render_review_report(_verdict(), _plan(), executive_summary=None)
    assert "## 🎯 Blocking Findings" in md
    assert "## ⚠️ Non-Blocking Issues" in md
    assert "None." in md
    assert "No watch items." in md
    # Executive Summary is always present (verdict label + TL;DR)
    assert "## 📊 Executive Summary" in md
    assert "No clear change intent was provided by the Judge output." in md


def test_report_renders_override_note_and_human_judgment():
    plan = _plan(
        safe_count=3,
        validated_safe_refs=[
            OverlayRef(source_agent="Security", finding_id="S-1", title="cleared"),
            OverlayRef(source_agent="PenTest", finding_id="S-2", title="cleared"),
            OverlayRef(source_agent="Security", finding_id="S-3", title="cleared"),
        ],
        needs_human_judgment_refs=[
            OverlayRef(
                source_agent="Historian",
                finding_id="H-1",
                reason="prior incident",
                title="A past revert",
            ),
        ],
    )
    md = render_review_report(_verdict(verdict_overridden=True), plan, executive_summary=None)
    assert "auto-overridden to REJECT due to a Critical finding" in md
    assert "## 🤔 Needs Human Judgment" in md
    assert "### H-1: A past revert" in md
    assert "**Source**: Historian" in md
    assert "**Reason**: prior incident" in md
    # Validated-safe count flows into Why-this-matters + Count Verification
    assert "3 items were reviewed and marked safe." in md
    assert "- Validated safe: 3" in md


def test_agent_display_falls_back_to_despaced_key_when_unknown():
    plan = _plan(blocking_findings=[_finding(id="COR-001", source_agents=("Some_Unknown_Agent",))])
    md = render_review_report(_verdict(), plan)
    # Unknown wave key → "_" replaced with spaces (display-name fallback).
    assert "**Agent**: Some Unknown Agent" in md


def test_judge_executive_summary_extraction():
    results = {"Judge": {"response": '{"verdict":"REJECT","executive_summary":"  Hello world.  "}'}}
    assert judge_executive_summary(results) == "Hello world."


def test_judge_executive_summary_missing_returns_none():
    assert judge_executive_summary({}) is None
    assert judge_executive_summary({"Judge": {"response": ""}}) is None
    assert judge_executive_summary({"Judge": {"response": "{}"}}) is None
