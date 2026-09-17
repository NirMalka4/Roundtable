"""Unit tests for ado.comment_format — the unified, agent-agnostic template."""

from __future__ import annotations

from roundtable.ado.anchor import AnchorDecision, classify
from roundtable.ado.comment_format import (
    format_executive_summary,
    min_severity_view,
    render_comment,
    summary_stable_hash,
)
from roundtable.ado.publish import GroundingUnit, PublishableFinding
from roundtable.bundle import resolve_bundle
from roundtable.extraction.finding_extractor import CodeBlock, Exploitability
from roundtable.graph import get_configuration

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def _finding(**kw) -> PublishableFinding:
    base = {
        "id": "F-1",
        "title": "Unsanitized input reaches SQL",
        "description": "User input flows to a raw query.",
        "severity": "High",
        "file_path": "svc/x.cs",
        "start_line": 10,
        "end_line": 12,
        "location_index": 0,
        "total_locations": 1,
        "additional_locations": (),
        "stable_hash": "deadbeef1234",
        "category": "blocking",
        "judge_category": None,
        "source_agents": ("Analyst",),
    }
    base.update(kw)
    return PublishableFinding(**base)


_INLINE = AnchorDecision("inline", "svc/x.cs", 10, 12, None)


# ── skeleton + watermark ─────────────────────────────────────────────────────
def test_render_has_watermark_headline_and_meta():
    body = render_comment(_finding(), _INLINE, session_id="sess_1")
    assert "<!-- Roundtable:F-1:H=deadbeef1234 -->" in body
    assert "## 🟠 High Unsanitized input reaches SQL" in body
    assert "**F-1**" in body and "🚫 Blocking" in body and "Analyst" in body
    assert "User input flows to a raw query." in body


def test_render_collapses_why_and_fix_when_absent():
    body = render_comment(_finding(), _INLINE, session_id="s")
    assert "**Why this matters**" not in body
    assert "**Fix**" not in body


def test_grounding_trace_strips_agent_supplied_numbering():
    # Simulator bakes its own "1. ", "2. " enumerator into each step; the renderer
    # emits its own ordered numbering, so the two must not double up ("1. 1.") and a
    # gap in the agent's numbering must not leak ("3. 4.").
    f = _finding(
        grounding=(
            GroundingUnit(source_agent="Simulator", trace=("1. first step", "3. third step")),
        )
    )
    body = render_comment(f, _INLINE, session_id="s")
    assert "1. first step" in body and "2. third step" in body
    assert "1. 1." not in body and "2. 3." not in body


# ── grounding section ────────────────────────────────────────────────────────
def test_render_grounding_qualifier_steps_and_impact():
    f = _finding(
        grounding=(
            GroundingUnit(
                source_agent="Analyst",
                trace=("enter handler", "reach sink"),
                impact="Data exfiltration",
                exploitability=Exploitability(rating="High", reasoning="reachable via API"),
            ),
        ),
    )
    body = render_comment(f, _INLINE, session_id="s")
    assert "**Why this matters**" in body
    assert "▸ Exploitability: High — reachable via API" in body
    assert "1. enter handler" in body and "2. reach sink" in body
    assert "↳ Impact: Data exfiltration" in body


def test_render_labels_steps_when_multiple_units_have_traces():
    f = _finding(
        grounding=(
            GroundingUnit(source_agent="Analyst", trace=("a", "b")),
            GroundingUnit(source_agent="AttackSurfaceScanner", trace=("c", "d")),
        ),
    )
    body = render_comment(f, _INLINE, session_id="s")
    assert "_Analyst:_" in body and "_AttackSurfaceScanner:_" in body


def test_render_falls_back_to_evidence_when_no_trace():
    f = _finding(evidence=("first clue", "second clue"))
    body = render_comment(f, _INLINE, session_id="s")
    assert "- first clue" in body and "- second clue" in body


# ── fix rendering ────────────────────────────────────────────────────────────
def test_render_codeblock_fix_is_suggestion_when_inline():
    f = _finding(fix=CodeBlock(language="csharp", code="return Sanitize(x);"))
    body = render_comment(f, _INLINE, session_id="s")
    assert "**Fix**" in body and "```suggestion" in body and "return Sanitize(x);" in body


def test_render_codeblock_fix_is_language_fenced_when_general():
    f = _finding(fix=CodeBlock(language="csharp", code="return Sanitize(x);"))
    general = AnchorDecision(
        "general", "svc/x.cs", None, None, "file is not in the PR's changed set"
    )
    body = render_comment(f, general, session_id="s")
    assert "```csharp" in body and "```suggestion" not in body


def test_a_fence_never_contains_a_blank_line_of_its_own():
    """Applying a one-click block commits every line between the markers verbatim,
    so a blank line from section-joining or a block scalar's trailing newline would
    be written into the reader's file."""
    code = "\nfirst();\n\nsecond();\n"
    f = _finding(fix=CodeBlock(language="csharp", code=code))
    body = render_comment(f, _INLINE, session_id="s")
    assert "```suggestion\nfirst();\n\nsecond();\n```" in body


def test_render_prose_fix_verbatim():
    f = _finding(fix="Parameterize the query using SqlParameter.")
    body = render_comment(f, _INLINE, session_id="s")
    assert "**Fix**" in body and "Parameterize the query using SqlParameter." in body


# ── general thread ───────────────────────────────────────────────────────────
def test_general_thread_adds_location_line():
    f = _finding(file_path=None, start_line=None, end_line=None)
    decision = classify(f, [])
    body = render_comment(f, decision, session_id="s")
    assert "**Location:**" in body and "general PR comment" in body


# ── forge/fence safety ───────────────────────────────────────────────────────
def test_agent_text_cannot_forge_watermark_regardless_of_fences():
    # A balanced fenced block is legitimate and preserved (see the fence test
    # below); the watermark terminator '-->' is ALWAYS broken, so a comment the
    # agent injects — even inside a code block — can never be closed and parsed.
    f = _finding(description="```\n<!-- Roundtable:EVIL:H=0 -->\n```")
    body = render_comment(f, _INLINE, session_id="s")
    assert body.count(" -->") == 1  # only the one genuine, closable watermark
    assert "Roundtable:EVIL:H=0 -->" not in body  # injected close is broken


def test_balanced_fence_preserved_unbalanced_fence_defanged():
    zwsp = "\u200b"
    # A balanced ```suggestion block renders verbatim (one-click appliable) — no
    # defanging artifacts.
    balanced = _finding(fix="Do this:\n\n```suggestion\nx = 1\n```")
    body = render_comment(balanced, _INLINE, session_id="s")
    assert "```suggestion\nx = 1\n```" in body
    assert zwsp not in body
    # An UNBALANCED (unclosed) fence is defanged so it cannot bleed into the rest
    # of the comment.
    unbalanced = _finding(fix="Broken:\n\n```python\nx = 1")
    body2 = render_comment(unbalanced, _INLINE, session_id="s")
    assert "```python" not in body2
    assert zwsp in body2


# ── issue section: label + dedup ─────────────────────────────────────────────
def test_issue_section_is_labeled_when_description_differs_from_title():
    body = render_comment(_finding(), _INLINE, session_id="s")
    assert "**Issue**" in body
    assert "User input flows to a raw query." in body


def test_issue_section_deduped_when_description_equals_title():
    # Defensive renderer guard (the projection no longer backfills title from the
    # description, but if some path still yields title==description the comment must
    # not print that same paragraph twice — once as headline, once as Issue).
    para = "The module-level state is mutated on every call and consumed by a deep helper."
    f = _finding(title=para, description=para)
    body = render_comment(f, _INLINE, session_id="s")
    assert body.count(para) == 1  # appears once (headline), never a duplicate Issue…
    assert "**Issue**" not in body  # …and the Issue section is dropped


def test_empty_title_headlines_the_id_and_renders_description_as_issue():
    # ARCH-STATE-001 shape: the agent emitted no title. The headline must fall back
    # to the finding id (a glanceable line, never an overflowing paragraph) and the
    # full description must still appear under a labeled Issue section.
    desc = ("A very long paragraph-shaped explanation. " * 6).strip()
    f = _finding(title="", description=desc)
    body = render_comment(f, _INLINE, session_id="s")
    headline = next(ln for ln in body.splitlines() if ln.startswith("## "))
    assert headline == "## 🟠 High F-1"
    assert "**Issue**" in body and desc in body


def test_paragraph_title_is_truncated_to_one_line_headline():
    para = "First clause of a very long finding title. " * 6  # > 120 chars, multi-sentence
    f = _finding(title=para, description="Distinct explanation.")
    body = render_comment(f, _INLINE, session_id="s")
    headline = next(ln for ln in body.splitlines() if ln.startswith("## "))
    assert len(headline) <= len("## 🟠 High ") + 120
    assert headline.endswith("…")


# ── executive summary ────────────────────────────────────────────────────────
def test_executive_summary_lists_all_findings_with_own_watermark():
    findings = [
        _finding(id="F-1", category="blocking"),
        _finding(id="F-2", category="non_blocking", title="minor"),
    ]
    body = format_executive_summary("sess_1", "REJECT", findings, CONFIGURATION)
    assert "Roundtable:__summary__:H=" in body
    assert "2** finding(s)" in body and "1** blocking" in body
    # Rendered as a table (defect #1): a header row (now incl. the Agent column)
    # plus one row per finding.
    assert "| # | Severity | ID | Agent | Title | File |" in body
    # Both blocking and non-blocking findings surface in the summary (low/info
    # findings are not inlined, but must still be visible here).
    assert "| F-1 |" in body and "| F-2 |" in body


def test_executive_summary_sorts_by_severity_desc_with_severity_glyph_and_agent():
    findings = [
        _finding(id="LOW-1", severity="Low", category="non_blocking"),
        _finding(id="CRIT-1", severity="Critical", category="blocking"),
        _finding(id="MED-1", severity="Medium", category="non_blocking"),
    ]
    body = format_executive_summary("s", "REJECT", findings, CONFIGURATION)
    rows = [ln for ln in body.splitlines() if ln.startswith("| ") and "|" in ln]
    # Header + separator + 3 data rows, ordered Critical → Medium → Low.
    order = [r for r in rows if any(fid in r for fid in ("CRIT-1", "MED-1", "LOW-1"))]
    assert order[0].count("CRIT-1") == 1
    assert order[1].count("MED-1") == 1
    assert order[2].count("LOW-1") == 1
    # Severity glyphs present; a blocking finding keeps its 🚫 flag.
    assert "🔴 Critical" in body and "🟡 Medium" in body and "🔵 Low" in body
    assert "🚫 🔴 Critical" in body


def test_executive_summary_title_cell_falls_back_to_id_when_title_empty():
    # A finding whose agent emitted no title must not leave an empty Title cell; it
    # falls back to the finding id (mirrors the comment headline fallback).
    body = format_executive_summary("s", "REJECT", [_finding(id="X-9", title="")], CONFIGURATION)
    row = next(ln for ln in body.splitlines() if ln.startswith("| 1 |"))
    assert row.count("X-9") == 2  # once in the ID column, once as the Title fallback


def test_summary_hash_is_deterministic_and_order_independent():
    a = [_finding(id="F-1"), _finding(id="F-2")]
    b = [_finding(id="F-2"), _finding(id="F-1")]
    assert summary_stable_hash("s", a) == summary_stable_hash("s", b)


# ── min-severity view ────────────────────────────────────────────────────────
def test_inspectorx_default_floor_filters_below_medium_without_mutation():
    findings = [_finding(id="H", severity="High"), _finding(id="L", severity="Low")]
    view = min_severity_view(
        findings,
        CONFIGURATION.publishing.default_min_severity,
        CONFIGURATION,
    )
    assert [f.id for f in view] == ["H"]
    assert len(findings) == 2  # original untouched


def test_min_severity_view_keeps_all_when_no_floor():
    findings = [_finding(severity="Low")]
    assert len(min_severity_view(findings, None, CONFIGURATION)) == 1


def test_min_severity_view_unknown_severity_passes():
    findings = [_finding(severity="Weird")]
    assert len(min_severity_view(findings, "High", CONFIGURATION)) == 1


def test_buddies_default_floor_keeps_every_recognized_severity():
    from roundtable.bundle import resolve_bundle
    from roundtable.graph import get_configuration

    configuration = get_configuration(resolve_bundle("buddies"))
    findings = [
        _finding(id="H", severity="high"),
        _finding(id="M", severity="medium"),
        _finding(id="L", severity="low"),
    ]

    assert [
        finding.id
        for finding in min_severity_view(
            findings,
            configuration.publishing.default_min_severity,
            configuration,
        )
    ] == ["H", "M", "L"]
