"""Unit tests for plugins.security_packs."""

from __future__ import annotations

import json

from roundtable.configs.inspectorx.plugins.security_packs import (
    CATEGORIES,
    OWASP_CATEGORY_MAP,
    build_security_focus_pack,
    extract_security_keyword_evidence,
    format_security_focus_pack_body,
    normalize_sec_range,
)

# ─── normalize_sec_range ────────────────────────────────────────────────────


def test_normalize_catalog_endash_form():
    # EN DASH catalog form canonicalises to SEC-NNN-SEC-MMM.
    assert normalize_sec_range("SEC-016\u2013035") == "SEC-016-SEC-035"


def test_normalize_sip_hyphen_form_is_idempotent():
    # SIP already emits SEC-016-SEC-035; the regex doesn't match, replace is a no-op.
    assert normalize_sec_range("SEC-016-SEC-035") == "SEC-016-SEC-035"


def test_catalog_and_sip_forms_compare_equal():
    catalog = CATEGORIES[1][2]  # "SEC-016\u2013035"
    assert normalize_sec_range(catalog) == normalize_sec_range("SEC-016-SEC-035")


# ─── keyword evidence ───────────────────────────────────────────────────────


def test_keyword_evidence_extracts_file_line_keyword():
    diff = "+++ b/auth.py\n@@ -1,0 +5,2 @@\n+def login(token):\n+    return token\n"
    ev = extract_security_keyword_evidence(diff)
    assert ev[0]["file"] == "auth.py"
    assert ev[0]["line"] == 5
    assert ev[0]["keyword"] == "token"


def test_keyword_evidence_skips_dev_null_and_caps_at_12():
    # 20 added lines each carrying a keyword → capped at 12.
    lines = ["+++ b/x.py", "@@ -0,0 +1,20 @@"] + ["+token here"] * 20
    ev = extract_security_keyword_evidence("\n".join(lines))
    assert len(ev) == 12


def test_keyword_evidence_dev_null_not_treated_as_file():
    diff = "+++ /dev/null\n@@ -1,1 +0,0 @@\n-secret = 1\n"
    assert extract_security_keyword_evidence(diff) == []


# ─── focus pack ─────────────────────────────────────────────────────────────

_SIP = json.dumps(
    {
        "selected_sec_checks": ["SEC-001-SEC-015", "SEC-036-SEC-060"],
        "priority_scenarios": ["injection via query param"],
    }
)


def test_focus_pack_marks_selected_relevant_others_skip():
    pack = build_security_focus_pack(_SIP, "")
    by_num = {c["num"]: c for c in pack["category_map"]}
    assert by_num[1]["status"] == "RELEVANT"  # SEC-001-015 selected
    assert by_num[3]["status"] == "RELEVANT"  # SEC-036-060 selected
    assert by_num[2]["status"] == "SKIP"  # not selected
    assert by_num[1]["reason"] == "Selected by Security Intent Profiler"
    assert by_num[2]["reason"] == "Filtered out by Security Intent Profiler"


def test_focus_pack_owasp_split_and_dedup():
    pack = build_security_focus_pack(_SIP, "")
    sel_ids = [m["id"] for m in pack["selected_owasp"]]
    exc_ids = [m["id"] for m in pack["excluded_owasp"]]
    assert "A07" in sel_ids and "A03" in sel_ids  # cat 1 + cat 3 relevant
    assert "A01" in exc_ids  # cat 2 excluded
    # no id appears in both lists; no duplicates within a list.
    assert set(sel_ids).isdisjoint(exc_ids)
    assert len(sel_ids) == len(set(sel_ids))


def test_focus_pack_keeps_sip_priority_scenarios():
    pack = build_security_focus_pack(_SIP, "")
    assert pack["priority_scenarios"] == ["injection via query param"]


def test_focus_pack_empty_selection_is_all_skip():
    pack = build_security_focus_pack('{"selected_sec_checks": []}', "")
    assert all(c["status"] == "SKIP" for c in pack["category_map"])
    assert all(
        c["reason"] == "No SEC ranges selected by Security Intent Profiler"
        for c in pack["category_map"]
    )
    assert pack["selected_owasp"] == []
    assert pack["selected_sec_checks"] == []
    # every mapped OWASP id lands in excluded.
    assert len(pack["excluded_owasp"]) == sum(len(v) for v in OWASP_CATEGORY_MAP.values())


def test_focus_pack_malformed_sip_degrades_to_all_skip():
    pack = build_security_focus_pack("not json at all", "")
    assert all(c["status"] == "SKIP" for c in pack["category_map"])
    assert pack["selected_sec_checks"] == []


def test_focus_pack_selected_sec_checks_normalized_and_deduped():
    sip = json.dumps({"selected_sec_checks": ["SEC-001-SEC-015", "SEC-001-SEC-015"]})
    pack = build_security_focus_pack(sip, "")
    assert pack["selected_sec_checks"] == ["SEC-001-SEC-015"]


def test_focus_pack_key_order_matches_ts_interface():
    pack = build_security_focus_pack(_SIP, "")
    assert list(pack.keys()) == [
        "selected_owasp",
        "selected_sec_checks",
        "priority_scenarios",
        "grep_evidence",
        "excluded_owasp",
        "category_map",
    ]


# ─── format body ────────────────────────────────────────────────────────────


def test_format_body_is_fenced_json_without_heading():
    pack = build_security_focus_pack(_SIP, "")
    body = format_security_focus_pack_body(pack)
    # the heading is supplied by the node's delivery_label, not the body.
    assert not body.startswith("## security_focus_pack")
    assert body.startswith("```json\n")
    assert body.endswith("\n```")
    # the fenced body round-trips to the same object.
    inner = body.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(inner) == pack


def test_format_body_preserves_endash_via_ensure_ascii_false():
    pack = build_security_focus_pack(_SIP, "")
    body = format_security_focus_pack_body(pack)
    # the literal EN DASH from the catalog secRange survives serialization.
    assert "\u2013" in body
    assert "\\u2013" not in body  # not escaped
