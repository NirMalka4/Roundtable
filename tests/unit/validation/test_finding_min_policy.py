"""Phase-7 policy tests for the baseline finding contract (finding_min).

These lock in the consolidated decision: an ``id`` is BLOCKING on every finding agent
(an id-less finding is silently dropped from the specialist finding index, so it can
never be referenced, counted or published), while the descriptive fields
(title/severity/description) are ADVISORY — enforced declaratively via a second,
warn-level ``json_schema`` gate pointed at ``finding_recommended.schema.yaml``.
"""

from __future__ import annotations

import json
from functools import partial

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.graph.model import get_configuration, register_config_plugins
from roundtable.graph.model import get_entry as _get_entry
from roundtable.validation.gates import load_gate_registry, load_schema_document
from roundtable.validation.pipeline import evaluate_agent_output as _evaluate_agent_output
from roundtable.validation.pipeline import run_pipeline

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
register_config_plugins(_CONFIG)
_SCHEMA_DIR = _CONFIG.root / "schemas"
_REGISTRY = load_gate_registry(_CONFIG.root / "gates.yaml")
get_entry = partial(_get_entry, config=_CONFIG)
evaluate_agent_output = partial(_evaluate_agent_output, configuration=_CONFIG)

_FINDING_MIN_AGENTS = [
    "CodeCorrectness",
    "Deadlock",
    "DocsKeeper",
    "TestQuality",
    "Dependency",
    "Architecture",
    "Privacy",
]

_CTX = {"changed_files": ["a.ts"]}


def _finding(**overrides):
    base = {
        "id": "F-1",
        "title": "t",
        "severity": "HIGH",
        "description": "d",
        "locations": [{"filePath": "a.ts", "startLine": 1, "endLine": 2}],
        "fix": "Add a unit test asserting the error EmptyState renders for a 403 response.",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("key", _FINDING_MIN_AGENTS)
def test_finding_min_agents_wire_the_warn_companion(key):
    entry = get_entry(key)
    assert entry is not None and entry.output_schema == "finding_min.schema.yaml"
    js_gates = [g for g in entry.ovg_gates if g.get("gate") == "json_schema"]
    # exactly one blocking (implicit level=error) + one warn companion
    assert len(js_gates) == 2, key
    companion = next(g for g in js_gates if g.get("level") == "warn")
    assert companion["params"]["schema"] == "finding_recommended.schema.yaml"
    assert companion["hint"] == "finding_recommended"


def test_idless_finding_is_blocking():
    idless = {"findings": [_finding()]}
    del idless["findings"][0]["id"]
    res = evaluate_agent_output("TestQuality", json.dumps(idless), _CTX)
    assert not res.passed
    assert res.gate == "json_schema"
    assert any("missing required field 'id'" in m for m in res.error_messages())


def test_missing_descriptive_fields_are_advisory_only():
    partial = {
        "findings": [
            {"id": "F-9", "locations": [{"filePath": "a.ts", "startLine": 1, "endLine": 2}]}
        ]
    }
    res = evaluate_agent_output("TestQuality", json.dumps(partial), _CTX)
    assert res.passed  # non-blocking
    warned = " ".join(res.warning_messages())
    assert "missing required field 'title'" in warned
    assert "missing required field 'description'" in warned


def test_complete_finding_has_no_recommended_warning():
    full = {"findings": [_finding()]}
    res = evaluate_agent_output("TestQuality", json.dumps(full), _CTX)
    assert res.passed
    assert not any("required field" in m for m in res.warning_messages())


def test_missing_fix_is_blocking_for_real_defect():
    """A severity>=low finding without a ``fix`` fails the error-level ``fix_present`` gate."""
    no_fix = {"findings": [_finding()]}
    del no_fix["findings"][0]["fix"]
    res = evaluate_agent_output("TestQuality", json.dumps(no_fix), _CTX)
    assert not res.passed
    assert res.gate == "fix_present"
    assert any("missing required 'fix'" in m for m in res.error_messages())


def test_missing_fix_is_exempt_for_info_finding():
    """``info`` severity is fix-exempt, mirroring ``severity_blocking``."""
    info_no_fix = {"findings": [_finding(severity="info")]}
    del info_no_fix["findings"][0]["fix"]
    res = evaluate_agent_output("TestQuality", json.dumps(info_no_fix), _CTX)
    assert res.passed


def test_sentence_valued_id_is_blocking():
    """An ``id`` is a slug (no whitespace, <=64 chars); a full-sentence id (e.g. a
    Simulator scenario leaking through) fails the BLOCKING json_schema gate instead of
    surfacing as a paragraph-length headline in the published comment."""
    sentence_id = _finding(
        id="High ioc-type-converters: @wcd/domain enum undefined/empty at first import"
    )
    res = evaluate_agent_output("TestQuality", json.dumps({"findings": [sentence_id]}), _CTX)
    assert not res.passed
    assert res.gate == "json_schema"
    assert any("findings[0].id" in m for m in res.error_messages())


def test_paragraph_length_title_is_advisory_only():
    """``title`` carries a one-line <=300-char ceiling on the WARN companion; a
    paragraph-length title draws an advisory diagnostic but does not block."""
    res = evaluate_agent_output(
        "TestQuality", json.dumps({"findings": [_finding(title="x" * 400)]}), _CTX
    )
    assert res.passed  # non-blocking
    assert any("findings[0].title" in m for m in res.warning_messages())


def test_run_pipeline_resolves_string_params_schema():
    """A gate may reference a companion schema by relpath in ``params.schema``; the
    pipeline resolves it to a schema document before running the json_schema gate."""
    recommended = load_schema_document("finding_recommended.schema.yaml", _SCHEMA_DIR)
    assert recommended  # sanity: the companion exists
    output = {"findings": [{"id": "F-1"}]}  # missing title/severity/description
    result = run_pipeline(
        "TestQuality",
        json.dumps(output),
        _CTX,
        output_schema=load_schema_document("finding_min.schema.yaml", _SCHEMA_DIR),
        ovg_gates=(
            {"gate": "json_schema"},
            {
                "gate": "json_schema",
                "level": "warn",
                "hint": "finding_recommended",
                "params": {"schema": "finding_recommended.schema.yaml"},
            },
        ),
        registry=_REGISTRY,
        schema_dir=_SCHEMA_DIR,
        configuration=_CONFIG,
    )
    assert result.passed  # id present -> error floor satisfied
    assert result.warnings  # recommended fields missing -> advisory
