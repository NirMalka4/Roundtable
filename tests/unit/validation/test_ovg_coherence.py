"""Coherence checks for the doctor: the generic schema↔gate consistency layer.

Two behavioural guarantees are pinned here:
  * the REAL config is fully coherent (zero errors, zero coverage warnings), so a
    regression that mis-wires or drops a gate turns red; and
  * each generic rule actually fires on a synthetic drift (proving the check is not a
    silent no-op) — a dead wired gate, an un-wired-but-declared gate (coverage), a
    missing schema/gates wiring, an unknown gate, a missing hint, and a bad example.

No agent or field names are asserted structurally: mutations are derived from the live
graph, so new agents are covered without editing this file.
"""

from __future__ import annotations

import dataclasses
import types

from roundtable.bundle import resolve_bundle
from roundtable.graph.model import get_configuration, register_config_plugins
from roundtable.validation.coherence import (
    _check_example,
    _check_output_example,
    _requires_sequence,
    schema_property_sequences,
)
from roundtable.validation.coherence import (
    _check_example_gate_valid as _check_example_gate_valid_explicit,
)
from roundtable.validation.coherence import (
    validate_ovg_coherence as _validate_ovg_coherence,
)
from roundtable.validation.gates import load_gate_registry, load_schema_document

_SCHEMA_DIR = resolve_bundle("inspectorx") / "schemas"
_CONFIG = get_configuration(resolve_bundle("inspectorx"))
register_config_plugins(_CONFIG)
_GATES = load_gate_registry(_CONFIG.root / "gates.yaml")


def validate_ovg_coherence(graph, **kwargs):
    return _validate_ovg_coherence(
        graph,
        configuration=_CONFIG,
        registry=_GATES,
        **kwargs,
    )


def _check_example_gate_valid(*args, **kwargs):
    return _check_example_gate_valid_explicit(*args, configuration=_CONFIG, **kwargs)


def _entry(pred):
    return next(e for e in _CONFIG.entries if e.is_llm and e.output_schema and pred(e))


def _gate_names(entry):
    return [g.get("gate") for g in (entry.ovg_gates or ())]


def _swap(entry):
    """Return a graph tuple with ``entry`` substituted in place by key."""
    return tuple(entry if e.key == entry.key else e for e in _CONFIG.entries)


# ── the real config is coherent ──────────────────────────────────────────────


def test_real_config_has_no_coherence_errors():
    report = validate_ovg_coherence(_CONFIG.entries)
    assert report.errors == []


def test_real_config_has_no_coverage_warnings():
    """Every agent that declares a gate's guarded fields already wires that gate; if
    this ever warns, either wire the gate or the schema drifted."""
    report = validate_ovg_coherence(_CONFIG.entries)
    assert report.warnings == []


# ── schema_property_sequences is required-aware and depth-honest ─────────────


def test_schema_sequences_are_required_aware():
    """A required key with no ``properties`` subschema (gate owns the internals) is
    still a declared path — but its internals are NOT invented."""
    schema = load_schema_document("security.schema.yaml", _SCHEMA_DIR)
    seqs = schema_property_sequences(schema)
    assert ("findings",) in seqs
    assert ("findings", "locations") in seqs  # required, even without a subschema
    # the gate-owned leaf under locations is deliberately absent from the schema
    assert not any(s[:2] == ("findings", "locations") and len(s) > 2 for s in seqs)


def test_requires_sequence_strips_array_markers():
    assert _requires_sequence("findings[].locations[].filePath") == (
        "findings",
        "locations",
        "filePath",
    )


# ── applicability (ERROR): a wired gate whose requires roots are absent is dead ──


def test_dead_wired_gate_is_an_error():
    sim = _entry(lambda e: "simulator" in e.output_schema)
    assert "findings" not in {
        s[0]
        for s in schema_property_sequences(load_schema_document(sim.output_schema, _SCHEMA_DIR))
    }
    mutated = dataclasses.replace(sim, ovg_gates=(*sim.ovg_gates, {"gate": "grounded_locations"}))
    report = validate_ovg_coherence(_swap(mutated))
    assert any("dead" in e and sim.key in e for e in report.errors)


# ── coverage (WARN): an un-wired gate whose fields the schema declares ──────────


def test_unwired_but_declared_gate_warns():
    finder = _entry(
        lambda e: (
            "grounded_locations" in _gate_names(e)
            and ("findings", "locations")
            in schema_property_sequences(load_schema_document(e.output_schema, _SCHEMA_DIR))
        )
    )
    mutated = dataclasses.replace(
        finder,
        ovg_gates=tuple(g for g in finder.ovg_gates if g.get("gate") != "grounded_locations"),
    )
    report = validate_ovg_coherence(_swap(mutated))
    assert report.errors == []  # dropping a gate is not itself an error
    assert any("grounded_locations" in w and finder.key in w for w in report.warnings)


# ── wiring completeness (ERROR) ──────────────────────────────────────────────


def test_is_llm_agent_without_schema_is_an_error():
    finder = _entry(lambda e: True)
    mutated = dataclasses.replace(finder, output_schema=None)
    report = validate_ovg_coherence(_swap(mutated))
    assert any(finder.key in e and "output_schema" in e for e in report.errors)


# ── reference integrity (ERROR): unknown gate + missing hint ─────────────────


def test_unknown_wired_gate_is_an_error():
    finder = _entry(lambda e: True)
    mutated = dataclasses.replace(finder, ovg_gates=(*finder.ovg_gates, {"gate": "no_such_gate"}))
    report = validate_ovg_coherence(_swap(mutated))
    assert any("no_such_gate" in e and finder.key in e for e in report.errors)


def test_missing_hint_override_is_an_error():
    finder = _entry(lambda e: True)
    gates = tuple(
        {**g, "hint": "definitely_not_a_hint"} if g.get("gate") == "json_schema" else g
        for g in finder.ovg_gates
    )
    mutated = dataclasses.replace(finder, ovg_gates=gates)
    report = validate_ovg_coherence(_swap(mutated))
    assert any("hint" in e and finder.key in e for e in report.errors)


# ── example validity (ERROR) ─────────────────────────────────────────────────


def test_example_that_violates_its_own_schema_is_an_error():
    schema = {
        "type": "object",
        "required": ["findings"],
        "properties": {"findings": {"type": "array"}},
        "examples": [{"findings": "not-an-array"}],
    }
    report = _check_example("Synthetic", schema, _SCHEMA_DIR)
    assert report.errors and "examples[0]" in report.errors[0]
    assert report.warnings == []


def test_missing_examples_warns():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    report = _check_example("Synthetic", schema, _SCHEMA_DIR)
    assert report.errors == []
    assert report.warnings and "examples" in report.warnings[0]


# ── examples[0] must pass its own context-free error gates (ERROR) ───────────


def test_example_failing_a_context_free_error_gate_is_an_error():
    """An ``examples[0]`` that self-validates against its schema but violates a wired
    ERROR gate (here ``locations_floor``: ``startLine`` below the ≥1 floor) is caught —
    the invariant ``--simulate`` relies on."""
    schema = {
        "type": "object",
        "required": ["findings"],
        "properties": {"findings": {"type": "array"}},
        "examples": [{"findings": [{"locations": [{"filePath": "a.py", "startLine": 0}]}]}],
    }
    entry = types.SimpleNamespace(
        key="Synthetic", ovg_gates=[{"gate": "locations_floor", "level": "error"}]
    )
    report = _check_example_gate_valid(entry, "Synthetic", schema, _GATES, _SCHEMA_DIR)
    assert report.errors and "context-free error gate" in report.errors[0]
    assert "locations_floor" in report.errors[0]


def test_context_gate_is_skipped_in_static_example_check():
    """A context gate (``grounded_locations``, needs the review diff) must NOT fire in the
    static check — it self-skips on empty context — so an ungroundable example path is not
    a doctor error (grounding is the mock's runtime job, not the example's)."""
    schema = {
        "type": "object",
        "required": ["findings"],
        "properties": {"findings": {"type": "array"}},
        "examples": [{"findings": [{"locations": [{"filePath": "not/in/any/diff.py"}]}]}],
    }
    entry = types.SimpleNamespace(
        key="Synthetic", ovg_gates=[{"gate": "grounded_locations", "level": "error"}]
    )
    report = _check_example_gate_valid(entry, "Synthetic", schema, _GATES, _SCHEMA_DIR)
    assert report.errors == []


def test_real_config_examples_pass_their_context_free_error_gates():
    """Every real is_llm agent's ``examples[0]`` passes its own context-free error gates —
    the statically-enforced happy-path invariant the simulate mock depends on."""
    for entry in _CONFIG.entries:
        if not (entry.is_llm and entry.output_schema):
            continue
        schema = load_schema_document(entry.output_schema, _SCHEMA_DIR)
        report = _check_example_gate_valid(entry, entry.key, schema, _GATES, _SCHEMA_DIR)
        assert report.errors == [], report.errors


# ── output_example validity (ERROR) ──────────────────────────────────────────

_EXAMPLE_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {"findings": {"type": "array"}},
}


def test_output_example_absent_is_noop():
    """No declared ``output_example`` → the check is silent (never invents an error)."""
    entry = types.SimpleNamespace(output_example=None)
    report = _check_output_example(entry, "Synthetic", _EXAMPLE_SCHEMA, _SCHEMA_DIR)
    assert report.errors == [] and report.warnings == []


def test_output_example_that_fails_to_load_is_an_error():
    """A declared-but-unloadable ``output_example`` is a coherence error, not a silent skip."""
    entry = types.SimpleNamespace(output_example="examples/does-not-exist.yaml")
    report = _check_output_example(entry, "Synthetic", _EXAMPLE_SCHEMA, _SCHEMA_DIR)
    assert report.errors and "cannot load output_example" in report.errors[0]


def test_output_example_violating_schema_is_an_error(tmp_path):
    """An ``output_example`` that drifts from ``output_schema`` is caught (would else make
    the LLM emit a shape the gate rejects)."""
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "bad.yaml").write_text("findings: not-an-array\n", encoding="utf-8")
    entry = types.SimpleNamespace(output_example="examples/bad.yaml")
    report = _check_output_example(entry, "Synthetic", _EXAMPLE_SCHEMA, tmp_path)
    assert report.errors and "does not validate against output_schema" in report.errors[0]


def test_valid_output_example_is_clean(tmp_path):
    """A conforming ``output_example`` produces no errors."""
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "ok.yaml").write_text("findings: []\n", encoding="utf-8")
    entry = types.SimpleNamespace(output_example="examples/ok.yaml")
    report = _check_output_example(entry, "Synthetic", _EXAMPLE_SCHEMA, tmp_path)
    assert report.errors == [] and report.warnings == []


# ── doctor integration ───────────────────────────────────────────────────────


def test_doctor_populates_warnings_sink_without_raising():
    """The doctor CLI populates its warnings sink and does not raise on a clean config."""
    from roundtable.runtime.agent_setup import validate_agents

    report = validate_agents(_CONFIG.root / "prompts" / "Reviewer", config=_CONFIG)
    assert report.ok  # clean config → no errors, and no exception raised
    assert report.warnings == []  # clean config → no advisories
