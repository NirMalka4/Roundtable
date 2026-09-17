"""Unit tests for build_specialist_finding_index."""

from __future__ import annotations

import json

from roundtable.configs.inspectorx.plugins.specialist_finding_index import (
    build_index_key,
    build_specialist_finding_index,
)


def _resp(obj) -> dict:
    return {"response": json.dumps(obj)}


def test_index_key_canonicalizes_agent():
    assert build_index_key("SchemaDrift", "SDR-001") == "schema_drift::SDR-001"


def test_index_key_unknown_agent_lowercases():
    assert build_index_key("MysteryAgent", "F-1") == "mysteryagent::F-1"


def test_build_index_skips_judge():
    results = {
        "SchemaDrift": _resp({"findings": [{"id": "SDR-1"}]}),
        "Judge": _resp(
            {"verdict_overlay": [{"source_agent": "SchemaDrift", "finding_id": "SDR-1"}]}
        ),
    }
    index = build_specialist_finding_index(results)
    keys = sorted(f"{r.source_agent_canonical}::{r.finding_id}" for r in index.all_records)
    assert keys == ["schema_drift::SDR-1"]
    assert "schema_drift::SDR-1" in index.by_key


def test_first_writer_wins_on_duplicate_key():
    results = {
        "SchemaDrift": _resp(
            {
                "findings": [
                    {"id": "SDR-1", "severity": "HIGH"},
                    {"id": "SDR-1", "severity": "LOW"},
                ]
            }
        ),
    }
    index = build_specialist_finding_index(results)
    assert len(index.all_records) == 1
    assert index.by_key["schema_drift::SDR-1"].finding.severity == "HIGH"


def test_empty_or_nonjson_responses_skipped():
    results = {
        "SchemaDrift": _resp({"findings": [{"id": "SDR-1"}]}),
        "DeterministicPreScan": {"response": "[OVG] prose"},
        "Optimizer": {"response": ""},
    }
    index = build_specialist_finding_index(results)
    assert len(index.all_records) == 1
