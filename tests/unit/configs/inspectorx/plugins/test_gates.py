"""Tests for the InspectorX bundle's OVG gate callables (``plugins/gates.py``)."""

from __future__ import annotations

from roundtable.bundle import resolve_bundle
from roundtable.configs.inspectorx.plugins.gates import fix_prose_shape_gate, generic_phrase_gate
from roundtable.graph import get_configuration, register_config_plugins
from roundtable.validation.gate_kit import GateRequest

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
register_config_plugins(_CONFIG)


def test_generic_phrase_gate_flags_boilerplate():
    output = {"findings": [{"description": "Consider adding null checks here."}]}
    diags = generic_phrase_gate(GateRequest("Security", output, {}))
    assert len(diags) == 1
    assert diags[0].path == "findings[0].description"


def test_generic_phrase_gate_passes_specific_description():
    output = {"findings": [{"description": "`pending_ids` is a list, so membership is O(m)."}]}
    assert generic_phrase_gate(GateRequest("Security", output, {})) == []


def test_generic_phrase_gate_prefers_ideal_code_would():
    output = {
        "findings": [{"ideal_code_would": "improve readability", "description": "a specific claim"}]
    }
    assert len(generic_phrase_gate(GateRequest("Security", output, {}))) == 1


def test_generic_phrase_gate_registered_under_manifest_fn_name():
    from roundtable.validation.gates import load_gate_registry

    assert (
        load_gate_registry(_CONFIG.root / "gates.yaml")["generic_phrase"].fn is generic_phrase_gate
    )


def test_fix_prose_shape_gate_flags_fence_in_prose_fix():
    output = {
        "findings": [
            {"id": "A", "fix": "Rename the symbol and update callers."},  # clean prose
            {"id": "B", "fix": "Apply this:\n```suggestion\nfoo()\n```"},  # fenced -> flagged
            {"id": "C", "fix": {"language": "ts", "code": "foo()"}},  # structured -> exempt
        ]
    }
    diags = fix_prose_shape_gate(GateRequest("TestQuality", output, {}))
    assert [d.path for d in diags] == ["findings[1].fix"]
    assert "code fence" in diags[0].message


def test_fix_prose_shape_gate_noop_when_no_fences():
    output = {"findings": [{"id": "A", "fix": "Guard the null case in resolveIocEnum()."}]}
    assert fix_prose_shape_gate(GateRequest("TestQuality", output, {})) == []


def test_fix_prose_shape_gate_flags_indented_raw_code():
    # The TQ-002 failure mode: a tab-indented code snippet in the string form, with NO
    # fence — Markdown renders the indented lines as a code block and column-0 lines as
    # prose (half in / half out). The fence-only check missed this.
    output = {
        "findings": [
            {"id": "A", "fix": "Add this test:\n\t[Fact]\n\tpublic void Foo() { }"},
        ]
    }
    diags = fix_prose_shape_gate(GateRequest("TestQuality", output, {}))
    assert [d.path for d in diags] == ["findings[0].fix"]
    assert "indented" in diags[0].message


def test_fix_prose_shape_gate_allows_multiline_prose():
    # Multi-line prose with no fence and no code-level indentation stays clean.
    output = {
        "findings": [
            {"id": "A", "fix": "Rename the symbol.\nThen update every caller in the module."},
        ]
    }
    assert fix_prose_shape_gate(GateRequest("TestQuality", output, {})) == []
