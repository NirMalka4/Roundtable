"""Unit tests for finding_extractor."""

from __future__ import annotations

import json
from functools import partial

from roundtable.bundle import resolve_bundle
from roundtable.extraction.finding_extractor import (
    extract_findings as _extract_findings,
)
from roundtable.extraction.finding_extractor import (
    synthesize_stable_id,
)
from roundtable.graph import get_configuration

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
extract_findings = partial(_extract_findings, configuration=_CONFIG)


def test_findings_key_basic_ids():
    resp = json.dumps(
        {
            "findings": [
                {"id": "F-1", "severity": "HIGH", "category": "Security"},
                {"id": "F-2", "severity": "low"},
            ]
        }
    )
    items = extract_findings("Analyst_Logic", resp)
    assert [f.id for f in items] == ["F-1", "F-2"]
    assert items[0].severity == "HIGH"
    assert items[1].category == "unknown"  # default


def test_unified_draft_and_legacy_remediation_variants_remain_extractable() -> None:
    locations = [{"filePath": "a.py", "startLine": 4, "endLine": 4}]
    common = {
        "rationale": "The change addresses the issue.",
        "checks": [
            {
                "filePath": "a.py",
                "startLine": 4,
                "endLine": 4,
                "observation": "The boundary owns the value.",
            }
        ],
        "limitations": [],
    }
    response = json.dumps(
        {
            "findings": [
                {
                    "id": "new",
                    "locations": locations,
                    "remediation": {
                        **common,
                        "proposal": "Normalize at the boundary.",
                        "illustration": "value = normalize(value)",
                        "language": "python",
                    },
                },
                {
                    "id": "suggestion",
                    "locations": locations,
                    "remediation": {
                        **common,
                        "kind": "suggestion",
                        "anchorIndex": 0,
                        "replacement": "value = normalize(value)",
                    },
                },
                {
                    "id": "fix",
                    "locations": locations,
                    "remediation": {**common, "kind": "fix", "prose": "Normalize it."},
                },
                {
                    "id": "draft",
                    "locations": locations,
                    "remediation": {
                        **common,
                        "kind": "draft",
                        "code": "normalize(value)",
                        "language": "python",
                    },
                },
            ]
        }
    )
    remediations = [item.remediation for item in extract_findings("legacy", response)]
    assert [
        (
            remediation.kind,
            getattr(remediation, "proposal", None),
            getattr(remediation, "illustration", None),
            remediation.replacement,
            remediation.prose,
            remediation.code,
        )
        for remediation in remediations
        if remediation is not None
    ] == [
        (None, "Normalize at the boundary.", "value = normalize(value)", None, None, None),
        ("suggestion", None, None, "value = normalize(value)", None, None),
        ("fix", None, None, None, "Normalize it.", None),
        ("draft", None, None, None, None, "normalize(value)"),
    ]


def test_inflated_findings_adapter():
    # SeverityInflator's inflated_findings adapter: id <- finding_id, description <- title.
    resp = json.dumps(
        {
            "inflated_findings": [
                {
                    "finding_id": "SDR-001",
                    "source_agent": "schema_drift",
                    "severity": "critical",
                    "title": "Column dropped without migration",
                },
            ]
        }
    )
    items = extract_findings("SeverityInflator", resp)
    assert items[0].id == "SDR-001"
    assert items[0].severity == "critical"
    assert items[0].description == "Column dropped without migration"


def test_findings_default_adapter_ignores_noncanonical_id_keys():
    # A plain `findings` array uses the default adapter (id <- id), so finding_id /
    # scenario are NOT id sources here — id-less rows get a synthesized stable id.
    resp = json.dumps(
        {
            "findings": [
                {"finding_id": "X-9"},
                {"scenario": "S-7"},
            ]
        }
    )
    items = extract_findings("Analyst_Logic", resp)
    assert all(f.id.startswith("Analyst_Logic:") for f in items)


def test_synthesized_id_when_no_explicit_id():
    resp = json.dumps(
        {
            "findings": [
                {
                    "description": "Race condition",
                    "locations": [{"filePath": "a.cs", "startLine": 10, "endLine": 10}],
                },
            ]
        }
    )
    items = extract_findings("Deadlock", resp)
    expected = synthesize_stable_id("Deadlock", "a.cs", 10, "Race condition")
    assert items[0].id == expected
    assert items[0].id.startswith("Deadlock:")


def test_security_cracks_adapter_defaults():
    # security_cracks adapter supplies severity/category defaults; an id-less crack
    # gets a synthesized stable id (no id_prefix synthesis anymore).
    resp = json.dumps(
        {
            "security_cracks": [
                {"title": "Auth bypass"},  # no id -> synthesized
                {"id": "C-2", "title": "Injection"},
            ]
        }
    )
    items = extract_findings("SecurityIntentProfiler", resp)
    assert items[0].id.startswith("SecurityIntentProfiler:")
    assert items[1].id == "C-2"
    assert all(f.severity == "medium" and f.category == "security_crack" for f in items)


def test_unmarked_nested_findings_not_collected():
    # Only schema-declared top-level x-finding-array properties are collected.
    # A findings[] nested under an arbitrary wrapper is not a declared finding
    # array, so it is ignored (the old speculative nested-fallback is gone).
    resp = json.dumps({"architecture_review": {"findings": [{"id": "A-1"}]}})
    items = extract_findings("Architecture", resp)
    assert items == []


def test_incident_warnings_not_collected():
    # Historian's incident_warnings is intentionally NOT marked x-finding-array,
    # so it produces no findings (previously it synthesized id-less phantoms).
    resp = json.dumps(
        {
            "incident_warnings": [
                {"file": "a.cs", "warning": "risky churn", "severity": "high"},
                {"file": "b.cs", "warning": "hotspot", "severity": "medium"},
            ]
        }
    )
    assert extract_findings("Historian", resp) == []


def test_annotation_array_not_collected():
    # pentest exploitability_assessment is x-finding-annotation -> not a finding.
    resp = json.dumps({"exploitability_assessment": [{"id": "PEN-1", "severity": "HIGH"}]})
    assert extract_findings("PenTest", resp) == []


def test_breakage_report_adapter_filter_severity_map_and_description():
    # Simulator breakage_report adapter: filter out PASS; a scenario is a full sentence
    # (not a slug), so no explicit id is mapped and the extractor synthesizes a short
    # stable id instead; title <- vulnerability (the weakness), description <- scenario
    # (the failing case); severity mapped from outcome.
    resp = json.dumps(
        {
            "breakage_report": [
                {"scenario": "S-1", "outcome": "FAILURE", "vulnerability": "boom"},
                {"scenario": "S-C", "outcome": "CRITICAL_FAILURE", "vulnerability": "worse"},
                {"scenario": "S-2", "outcome": "PASS"},  # skipped
            ]
        }
    )
    items = extract_findings("Simulator", resp)
    # id is synthesized (never the scenario sentence) and is a short slug: prefixed,
    # no whitespace, well under the 64-char id ceiling.
    assert [f.id for f in items] == [
        synthesize_stable_id("Simulator", None, None, "S-1"),
        synthesize_stable_id("Simulator", None, None, "S-C"),
    ]
    assert all(" " not in f.id and len(f.id) <= 64 for f in items)
    assert [f.title for f in items] == ["boom", "worse"]  # title <- vulnerability
    assert [f.description for f in items] == ["S-1", "S-C"]  # description <- scenario
    assert items[0].severity == "high"  # FAILURE -> high
    assert items[1].severity == "critical"  # CRITICAL_FAILURE -> critical


def test_non_json_yields_empty():
    assert extract_findings("DeterministicPreScan", "[OVG] not json") == []


def test_judge_category_snake_only():
    # judge_category reads the canonical snake_case key only; camelCase is not aliased.
    resp = json.dumps(
        {
            "findings": [
                {"id": "J-1", "judgeCategory": "Security", "category": "loose"},
                {"id": "J-2", "judge_category": "Architecture"},
            ]
        }
    )
    items = extract_findings("Analyst_Patterns", resp)
    assert items[0].judge_category is None  # camelCase not read
    assert items[1].judge_category == "Architecture"


def test_synthesize_stable_id_deterministic():
    a = synthesize_stable_id("Ag", "f.cs", 3, "Title.")
    b = synthesize_stable_id("Ag", "f.cs", 3, "Title")  # trailing punct normalised
    assert a == b
    assert len(a.split(":")[1]) == 12
