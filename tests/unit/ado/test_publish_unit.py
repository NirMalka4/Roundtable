"""Unit tests for the publish plan extractor's deterministic helpers.

Covers the surfaces the oracle corpus does NOT exercise to non-trivial values
(``check_count_parity`` aborts, ``compute_stable_hash`` algorithm, prose
detection, severity override) so a regression fails loudly rather than silently
passing the all-zero corpus.
"""

from __future__ import annotations

import hashlib

from roundtable.ado.publish import (
    PublishableFinding,
    compute_stable_hash,
    locations_of,
    watermark_hashed,
)
from roundtable.configs.inspectorx.plugins.verdict_overlay import (
    PublishPlan,
    PublishPlanDiagnostics,
    UnresolvedOverlayRef,
    check_count_parity,
    dedup_locations,
    detect_prose_in_code_block,
    extract_publish_plan,
    max_severity,
    normalize_severity,
)
from roundtable.extraction.finding_extractor import (
    CodeBlock,
    FindingItem,
    NormalizedLocation,
)


def _finding(**kw) -> PublishableFinding:
    base = {
        "id": "F-001",
        "title": "t",
        "description": "t",
        "severity": "Medium",
        "file_path": "a.cs",
        "start_line": 5,
        "end_line": 5,
        "location_index": 0,
        "total_locations": 1,
        "additional_locations": (),
        "stable_hash": "abc",
        "category": "non_blocking",
        "judge_category": None,
        "source_agents": ("Analyst",),
    }
    base.update(kw)
    return PublishableFinding(**base)


def _plan(**kw) -> PublishPlan:
    base = {
        "verdict": "APPROVE",
        "verdict_icon": "OK",
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


# ── compute_stable_hash ──────────────────────────────────────────────────────
def test_stable_hash_matches_ts_algorithm():
    expected = hashlib.sha1(b"f-001|medium|title").hexdigest()[:12]
    assert compute_stable_hash(["F-001", "Medium", " Title "]) == expected


def test_stable_hash_order_is_load_bearing():
    h1 = compute_stable_hash(["F", "Low", "t", "a.cs:1:1", "b.cs:2:2"])
    h2 = compute_stable_hash(["F", "Low", "t", "b.cs:2:2", "a.cs:1:1"])
    assert h1 != h2  # proves the caller's sort of the loc-sig is load-bearing


def test_stable_hash_none_parts_become_empty():
    assert compute_stable_hash(["a", None, "b"]) == compute_stable_hash(["a", "", "b"])


def test_watermark_hashed_format():
    assert watermark_hashed("F-001", "abc123def456") == "<!-- Roundtable:F-001:H=abc123def456 -->"


# ── normalize / max severity ─────────────────────────────────────────────────
def test_normalize_severity_default_info():
    assert normalize_severity("CRITICAL") == "Critical"
    assert normalize_severity("weird") == "Info"
    assert normalize_severity(None) == "Info"


def test_max_severity_picks_highest():
    assert max_severity(["low", "High", "medium"]) == "High"
    assert max_severity([None, None]) == "Low"  # default when nothing present


# ── locations ────────────────────────────────────────────────────────────────
def test_locations_of_prefers_structured():
    f = FindingItem(
        agent_name="A",
        id="x",
        severity="low",
        category="c",
        locations=(NormalizedLocation("a.cs", 3, 4),),
        file="b.cs",
        line=9,
    )
    locs = locations_of(f)
    assert len(locs) == 1
    assert locs[0].file_path == "a.cs" and locs[0].start_line == 3 and locs[0].end_line == 4


def test_locations_of_falls_back_to_file_line():
    f = FindingItem(
        agent_name="A", id="x", severity="low", category="c", file="b.cs", line=9, line_end=11
    )
    assert locations_of(f) == [NormalizedLocation("b.cs", 9, 11)]


def test_dedup_locations_preserves_order():
    locs = [
        NormalizedLocation("a", 1, 1),
        NormalizedLocation("a", 1, 1),
        NormalizedLocation("b", 2, 2),
    ]
    assert dedup_locations(locs) == [NormalizedLocation("a", 1, 1), NormalizedLocation("b", 2, 2)]


# ── prose detection ──────────────────────────────────────────────────────────
def test_prose_plaintext_on_code_file():
    assert (
        detect_prose_in_code_block("do the thing", "plaintext", "x.cs") == "plaintext_on_code_file"
    )


def test_prose_heuristic_imperative():
    assert (
        detect_prose_in_code_block("Remove one of the two tests.", "text", "x.cs")
        == "heuristic_prose_imperative"
    )


def test_prose_keeps_real_code():
    assert detect_prose_in_code_block("var x = 1;", "text", "x.cs") is None
    assert detect_prose_in_code_block("return null", "text", "x.cs") is None  # no terminal punct


def test_prose_skips_markdown_files():
    assert detect_prose_in_code_block("Remove the note.", "text", "x.md") is None


# ── count parity ─────────────────────────────────────────────────────────────
def test_count_parity_ok():
    f = _finding()
    plan = _plan(
        all_findings=[f],
        non_blocking_findings=[f],
        published_primary_count=1,
        original_findings_count=1,
    )
    assert check_count_parity(plan) is None


def test_count_parity_violation_on_unresolved_primary():
    plan = _plan(all_findings=[], published_primary_count=1, original_findings_count=1)
    err = check_count_parity(plan)
    assert err is not None and "Count parity violation" in err


def test_count_parity_aborts_on_unresolved_refs():
    f = _finding()
    plan = _plan(
        all_findings=[f],
        non_blocking_findings=[f],
        published_primary_count=1,
        original_findings_count=1,
        unresolved_refs=[UnresolvedOverlayRef("validated_safe", "X", "V-001")],
    )
    err = check_count_parity(plan)
    assert err is not None and "Overlay resolution failure" in err


def test_count_parity_aborts_on_judge_count_mismatch():
    f = _finding()
    plan = _plan(
        all_findings=[f],
        non_blocking_findings=[f],
        published_primary_count=1,
        original_findings_count=3,
    )
    err = check_count_parity(plan)
    assert err is not None and "Judge count mismatch" in err


# ── full extractor on a tiny synthetic session ───────────────────────────────
def test_extract_plan_resolves_overlay_and_hashes():
    import json

    specialist = {
        "response": json.dumps(
            {
                "findings": [
                    {
                        "id": "A-001",
                        "title": "Short title",
                        "description": "long desc",
                        "severity": "high",
                        "locations": [{"filePath": "svc/x.cs", "startLine": 10, "endLine": 12}],
                    }
                ]
            }
        )
    }
    judge = {
        "response": json.dumps(
            {
                "verdict": "APPROVE_WITH_SUGGESTIONS",
                "verdict_overlay": [
                    {"source_agent": "Analyst", "finding_id": "A-001", "blocking": False}
                ],
            }
        )
    }
    results = {"Analyst": specialist, "Judge": judge}
    plan = extract_publish_plan(results, session_dir_path="/x/session_test")
    assert plan is not None
    assert len(plan.all_findings) == 1
    f = plan.all_findings[0]
    assert f.id == "A-001"
    assert f.title == "Short title"
    assert f.severity == "High"
    assert f.file_path == "svc/x.cs" and f.start_line == 10 and f.end_line == 12
    expected = compute_stable_hash(["A-001", "High", "Short title", "svc/x.cs:10:12"])
    assert f.stable_hash == expected
    assert check_count_parity(plan) is None


def test_extract_plan_none_without_judge():
    assert extract_publish_plan({"Analyst": {"response": "{}"}}) is None


def test_extract_plan_titleless_finding_keeps_title_empty_not_backfilled():
    # A finding whose agent emitted no title must NOT have its paragraph-length
    # description promoted to the title (which would overflow the headline and drop
    # the Issue section). The title stays empty; the description is preserved.
    import json

    para = "A paragraph-length explanation that must never become the headline." * 3
    specialist = {
        "response": json.dumps(
            {
                "findings": [
                    {
                        "id": "A-001",
                        "description": para,
                        "severity": "high",
                        "locations": [{"filePath": "svc/x.cs", "startLine": 10, "endLine": 12}],
                    }
                ]
            }
        )
    }
    judge = {
        "response": json.dumps(
            {
                "verdict": "APPROVE_WITH_SUGGESTIONS",
                "verdict_overlay": [
                    {"source_agent": "Analyst", "finding_id": "A-001", "blocking": False}
                ],
            }
        )
    }
    plan = extract_publish_plan({"Analyst": specialist, "Judge": judge})
    assert plan is not None
    f = plan.all_findings[0]
    assert f.title == ""
    assert f.description == para


def test_extract_plan_severity_override_changes_hash():
    import json

    specialist = {
        "response": json.dumps({"findings": [{"id": "A-1", "title": "t", "severity": "low"}]})
    }
    judge = {
        "response": json.dumps(
            {
                "verdict": "REJECT",
                "verdict_overlay": [
                    {
                        "source_agent": "Analyst",
                        "finding_id": "A-1",
                        "blocking": True,
                        "verdict_severity": "critical",
                    }
                ],
            }
        )
    }
    plan = extract_publish_plan({"Analyst": specialist, "Judge": judge})
    assert plan is not None
    f = plan.all_findings[0]
    assert f.severity == "Critical"
    assert f.stable_hash == compute_stable_hash(["A-1", "Critical", "t"])


def test_extract_plan_captures_fix_and_grounding_per_agent():
    """Grounding trace/impact/exploitability are carried as WHOLE per-agent units
    (E4 — never step-merged across siblings), and the canonical fix is preserved.
    The watermark hash must ignore grounding entirely (byte-stability)."""
    import json

    specialist = {
        "response": json.dumps(
            {
                "findings": [
                    {
                        "id": "A-001",
                        "title": "t",
                        "severity": "high",
                        "locations": [{"filePath": "svc/x.cs", "startLine": 10, "endLine": 12}],
                        "fix": {"language": "csharp", "code": "return Sanitize(x);"},
                        "trace": ["enter handler", "reach sink"],
                        "impact": "RCE on the host",
                        "exploitability": {"rating": "High", "reasoning": "reachable via API"},
                    }
                ]
            }
        )
    }
    sibling = {
        "response": json.dumps(
            {
                "findings": [
                    {
                        "id": "B-001",
                        "title": "t2",
                        "severity": "medium",
                        "trace": ["different entrypoint", "different sink"],
                    }
                ]
            }
        )
    }
    judge = {
        "response": json.dumps(
            {
                "verdict": "REJECT",
                "verdict_overlay": [
                    {
                        "source_agent": "Analyst",
                        "finding_id": "A-001",
                        "blocking": True,
                        "merged_with": [
                            {"source_agent": "AttackSurfaceScanner", "finding_id": "B-001"}
                        ],
                    }
                ],
            }
        )
    }
    plan = extract_publish_plan(
        {"Analyst": specialist, "AttackSurfaceScanner": sibling, "Judge": judge}
    )
    assert plan is not None
    f = plan.all_findings[0]
    # Canonical fix carried onto the publish payload.
    assert isinstance(f.fix, CodeBlock)
    assert f.fix.language == "csharp" and "Sanitize" in f.fix.code
    # Two provenance-tagged grounding units — NOT one merged chain (E4).
    assert len(f.grounding) == 2
    by_agent = {u.source_agent: u for u in f.grounding}
    assert by_agent["Analyst"].trace == ("enter handler", "reach sink")
    assert by_agent["Analyst"].impact == "RCE on the host"
    assert by_agent["Analyst"].exploitability is not None
    assert by_agent["Analyst"].exploitability.rating == "High"
    assert by_agent["AttackSurfaceScanner"].trace == (
        "different entrypoint",
        "different sink",
    )
    # Grounding does not perturb the watermark hash.
    assert f.stable_hash == compute_stable_hash(["A-001", "High", "t", "svc/x.cs:10:12"])
