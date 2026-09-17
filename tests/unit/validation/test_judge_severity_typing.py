"""Judge severity is a schema-enforced vocabulary enum, not a case-insensitive gate.

Track B consolidation: ``verdict_overlay[].verdict_severity`` and
``judge_observations[].severity`` are typed via ``$ref severity_blocking`` in
``judge.schema.yaml`` and enforced by the (error-level) ``json_schema`` gate — exactly
like every other agent's severity. The ``judge_entries`` gate no longer owns the
severity enum, so no case-insensitive ``sev.lower()`` workaround papers over drift:
canonical lowercase is required and uppercase/``info`` are rejected.
"""

from __future__ import annotations

import json
from functools import partial

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration
from roundtable.validation.pipeline import evaluate_agent_output as _evaluate_agent_output

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
evaluate_agent_output = partial(_evaluate_agent_output, configuration=_CONFIG)

_CTX: dict = {}


def _overlay_entry(**overrides):
    entry = {
        "finding_id": "SEC-1",
        "source_agent": "security",
        "blocking": False,
        "judge_justification": "justified",
    }
    entry.update(overrides)
    return entry


def _observation(**overrides):
    obs = {"id": "JO-1", "severity": "high", "summary": "s", "blocking": True}
    obs.update(overrides)
    return obs


def _judge(**overrides):
    base = {
        "verdict": "APPROVE",
        "executive_summary": "summary",
        "architectural_assessment": {"consistency_check": "ok"},
        "plan_compliance": {"compliance_status": "COMPLIANT"},
        "verdict_overlay": [],
        "validated_safe": [],
        "needs_human_judgment": [],
        "count_verification": {},
    }
    base.update(overrides)
    return base


def _eval(output):
    return evaluate_agent_output("Judge", json.dumps(output), _CTX)


# ── verdict_severity ─────────────────────────────────────────────────────────


def test_lowercase_verdict_severity_passes():
    res = _eval(_judge(verdict_overlay=[_overlay_entry(verdict_severity="low")]))
    assert res.passed, res.error_messages()


def test_null_verdict_severity_passes():
    res = _eval(_judge(verdict_overlay=[_overlay_entry(verdict_severity=None)]))
    assert res.passed, res.error_messages()


def test_absent_verdict_severity_passes():
    res = _eval(_judge(verdict_overlay=[_overlay_entry()]))
    assert res.passed, res.error_messages()


def test_uppercase_verdict_severity_rejected_by_schema():
    res = _eval(_judge(verdict_overlay=[_overlay_entry(verdict_severity="HIGH")]))
    assert not res.passed
    assert res.gate == "json_schema"
    assert any("verdict_severity" in m for m in res.error_messages())


def test_info_verdict_severity_rejected_as_non_blocking():
    # `info` is a valid severity but NOT a blocking severity — a verdict overlay
    # entry may only carry a blocking severity.
    res = _eval(_judge(verdict_overlay=[_overlay_entry(verdict_severity="info")]))
    assert not res.passed
    assert any("verdict_severity" in m for m in res.error_messages())


# ── judge_observations[].severity ────────────────────────────────────────────


def test_lowercase_observation_severity_passes():
    res = _eval(_judge(judge_observations=[_observation(severity="critical")]))
    assert res.passed, res.error_messages()


def test_uppercase_observation_severity_rejected_by_schema():
    res = _eval(_judge(judge_observations=[_observation(severity="HIGH")]))
    assert not res.passed
    assert res.gate == "json_schema"
    assert any("severity" in m for m in res.error_messages())


def test_missing_observation_severity_rejected_by_schema():
    obs = _observation()
    del obs["severity"]
    res = _eval(_judge(judge_observations=[obs]))
    assert not res.passed
    assert res.gate == "json_schema"


def test_info_observation_severity_allowed_prompt_only_ceiling():
    # `severity` on an observation refs the full `severity` vocabulary term (coherence:
    # a field named `severity` must $ref `severity`). The Judge prompt narrows it to
    # blocking severities editorially — that ceiling is prompt-only, not schema-enforced,
    # exactly like every other per-agent severity ceiling — so `info` is schema-valid.
    res = _eval(_judge(judge_observations=[_observation(severity="info")]))
    assert res.passed, res.error_messages()
