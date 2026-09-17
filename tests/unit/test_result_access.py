"""Dual-shape result-access contract test.

Two shapes for session results flow through the pipeline: ``AgentRunOutcome``
objects (live review) and ``{"response": str}`` dicts (raw_results.json). Every
response consumer must agree across both. Feed the SAME logical session through
each consumer in both shapes and assert identical results, so a consumer that
handles only one shape (the empty-verdict regression) fails loudly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.configs.inspectorx.plugins.specialist_finding_index import (
    build_specialist_finding_index,
)
from roundtable.configs.inspectorx.plugins.verdict import compute_verdict
from roundtable.configs.inspectorx.plugins.verdict_overlay import extract_publish_plan
from roundtable.graph import get_configuration
from roundtable.result_access import response_of, to_response_map

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


@dataclass
class _Outcome:
    """Minimal AgentRunOutcome-like object exposing ``.response``."""

    response: str


def _judge_json() -> str:
    return json.dumps(
        {
            "verdict": "REJECT",
            "verdict_overlay": [
                {"source_agent": "SchemaDrift", "finding_id": "SDR-1", "blocking": True}
            ],
        }
    )


def _specialist_json() -> str:
    return json.dumps({"findings": [{"id": "SDR-1", "severity": "HIGH"}]})


def _as_objects() -> dict[str, _Outcome]:
    return {"SchemaDrift": _Outcome(_specialist_json()), "Judge": _Outcome(_judge_json())}


def _as_dicts() -> dict[str, dict[str, str]]:
    return {"SchemaDrift": {"response": _specialist_json()}, "Judge": {"response": _judge_json()}}


@pytest.fixture(params=["objects", "dicts"], ids=["AgentRunOutcome", "raw_results"])
def session(request):
    return _as_objects() if request.param == "objects" else _as_dicts()


def test_response_of_both_shapes():
    assert response_of(_Outcome("x")) == "x"
    assert response_of({"response": "x"}) == "x"
    assert response_of(_Outcome("")) is None
    assert response_of({"response": ""}) is None
    assert response_of(None) is None
    assert response_of({}) is None


def test_to_response_map_normalizes():
    assert to_response_map(_as_objects()) == to_response_map(_as_dicts()) == _as_dicts()


def test_compute_verdict_both_shapes(session):
    assert compute_verdict(session, CONFIGURATION).verdict == "REJECT"


def test_index_both_shapes(session):
    index = build_specialist_finding_index(session)
    assert "schema_drift::SDR-1" in index.by_key


def test_publish_plan_both_shapes(session):
    plan = extract_publish_plan(session)
    assert plan is not None
    assert len(plan.blocking_findings) == 1
    assert plan.blocking_findings[0].id == "SDR-1"
