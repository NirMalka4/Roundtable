"""Phase-1 scaffold tests for the consolidated OVG gate registry (validation/gates.py).

These prove the new infrastructure in isolation — the legacy OVG pipeline is
untouched and still authoritative until Phase 3 proves equivalence.
"""

from __future__ import annotations

import pytest
import yaml

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration, register_config_plugins
from roundtable.validation import gates
from roundtable.validation.gate_kit import GateRequest

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
register_config_plugins(_CONFIG)
_GATES_MANIFEST = _CONFIG.root / "gates.yaml"
_HINT_DIR = _CONFIG.root / "hints"
_SCHEMA_DIR = _CONFIG.root / "schemas"


# ─── registry + hints load from the shipped config ──────────────────────────
def test_registry_loads_shipped_manifest():
    reg = gates.load_gate_registry(_GATES_MANIFEST)
    assert set(reg) >= {"json_schema", "grounded_locations", "generic_phrase"}
    js = reg["json_schema"]
    assert js.default_level == "error"
    assert js.default_hint == "json_schema"
    gl = reg["grounded_locations"]
    assert gl.default_level == "warn"
    assert gl.requires == ("findings[].locations[].filePath",)


def test_every_default_hint_resolves():
    reg = gates.load_gate_registry(_GATES_MANIFEST)
    for spec in reg.values():
        if spec.default_hint:
            assert gates.load_hint(spec.default_hint, hint_dir=_HINT_DIR).strip()


def test_load_gate_registry_rejects_unknown_fn(tmp_path):
    p = tmp_path / "gates.yaml"
    p.write_text(yaml.safe_dump({"gates": {"x": {"fn": "nope"}}}), encoding="utf-8")
    with pytest.raises(gates.SchemaLoadError, match="unknown fn"):
        gates.load_gate_registry(p)


def test_load_gate_registry_rejects_bad_level(tmp_path):
    p = tmp_path / "gates.yaml"
    p.write_text(
        yaml.safe_dump({"gates": {"grounded_locations": {"default_level": "boom"}}}),
        encoding="utf-8",
    )
    with pytest.raises(gates.SchemaLoadError, match="invalid default_level"):
        gates.load_gate_registry(p)


# ─── G4: universal (engine-core) vs domain (bundle) gate split ───────────────
def test_universal_json_schema_is_engine_core_not_bundle():
    """json_schema is composed in from gates_core.yaml, not the bundle manifest."""
    bundle = yaml.safe_load(_GATES_MANIFEST.read_text(encoding="utf-8"))["gates"]
    assert "json_schema" not in bundle  # domain manifest must not carry it
    assert "json_schema" in gates.load_gate_registry(_GATES_MANIFEST)


def test_domain_manifest_may_be_empty(tmp_path):
    """A bundle wiring only universal gates (json_schema) has no domain gates — an
    empty domain manifest resolves to {} and the composed registry still has the core."""
    p = tmp_path / "gates.yaml"
    p.write_text(yaml.safe_dump({"gates": {}}), encoding="utf-8")
    reg = gates.load_gate_registry(p)
    assert "json_schema" in reg  # engine-core universal gate still composed in


def test_universal_hint_resolves_from_engine_core(tmp_path):
    """A universal gate's hint is found even when the bundle ships no such hint file."""
    (tmp_path / "grounded_locations.md").write_text("x", encoding="utf-8")  # domain only
    text = gates.load_hint("json_schema", hint_dir=tmp_path)
    assert text.strip()
    assert (gates._CORE_HINT_DIR / "json_schema.md").is_file()


def test_bundle_cannot_redefine_universal_gate(tmp_path):
    p = tmp_path / "gates.yaml"
    p.write_text(
        yaml.safe_dump({"gates": {"json_schema": {"fn": "json_schema"}}}), encoding="utf-8"
    )
    with pytest.raises(gates.SchemaLoadError, match="redefine universal"):
        gates.load_gate_registry(p)


# ─── json_schema gate: $ref, pass/fail, deterministic ordering ──────────────
def _write(tmp_path, name, doc):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


def _finding_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["findings"],
        "properties": {
            "findings": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["id", "severity", "locations"],
                    "properties": {
                        "id": {"type": "string"},
                        "severity": {"enum": ["LOW", "HIGH"]},
                        "locations": {
                            "type": "array",
                            "items": {"$ref": "_shared/location.schema.yaml"},
                        },
                    },
                },
            }
        },
    }


def test_json_schema_gate_passes_valid(tmp_path):
    _write(
        tmp_path,
        "_shared/location.schema.yaml",
        yaml.safe_load(
            (_SCHEMA_DIR / "_shared" / "location.schema.yaml").read_text(encoding="utf-8")
        ),
    )
    schema = _finding_schema()
    output = {"findings": [{"id": "F1", "severity": "HIGH", "locations": [{"filePath": "a.cs"}]}]}
    diags = gates.json_schema_gate(
        GateRequest("Security", output, {}, schema=schema, schema_dir=tmp_path)
    )
    assert diags == []


def test_json_schema_gate_reports_errors_deterministically(tmp_path):
    _write(
        tmp_path,
        "_shared/location.schema.yaml",
        yaml.safe_load(
            (_SCHEMA_DIR / "_shared" / "location.schema.yaml").read_text(encoding="utf-8")
        ),
    )
    schema = _finding_schema()
    # findings[0] missing `severity` and `locations` bad type; findings empty-invalid too
    output = {"findings": [{"id": "F1", "severity": "NOPE", "locations": [{}]}]}
    diags = gates.json_schema_gate(
        GateRequest("Security", output, {}, schema=schema, schema_dir=tmp_path)
    )
    assert diags, "expected schema errors"
    # deterministic ordering: sorted by (path, message)
    assert diags == sorted(diags, key=lambda d: (d.path, d.message))
    joined = " | ".join(f"{d.path}: {d.message}" for d in diags)
    assert "not in allowed set" in joined  # enum failure, project-owned phrasing
    assert "missing required field 'filePath'" in joined  # via $ref fragment


def test_compile_validator_rejects_remote_ref(tmp_path):
    schema = {"$ref": "https://evil.example/x.json"}
    with pytest.raises(gates.SchemaLoadError, match="remote"):
        gates.compile_validator(schema, schema_dir=tmp_path)


# ─── context gates return NEUTRAL diagnostics ───────────────────────────────
def test_grounded_locations_gate_flags_ungrounded():
    output = {
        "findings": [
            {"locations": [{"filePath": "src/a.cs"}]},
            {"locations": [{"filePath": "other/b.cs"}]},
        ]
    }
    ctx = {"changed_files": ["src/a.cs"]}
    diags = gates.grounded_locations_gate(GateRequest("Security", output, ctx))
    assert len(diags) == 1
    assert diags[0].path == "findings[1].locations[0].filePath"


def test_grounded_locations_gate_noop_without_changed_files():
    output = {"findings": [{"locations": [{"filePath": "x.cs"}]}]}
    assert gates.grounded_locations_gate(GateRequest("Security", output, {})) == []


def test_grounded_locations_gate_grounds_anchors_via_requires():
    # Buddies uses `findings[].anchors[].filePath`; the gate derives the array
    # field from `requires`, so it must flag an ungrounded anchor (was a no-op).
    output = {
        "findings": [
            {
                "anchors": [
                    {"filePath": "src/a.ts"},
                    {"filePath": "ghost/b.ts"},
                ]
            },
        ]
    }
    ctx = {"changed_files": ["src/a.ts"]}
    diags = gates.grounded_locations_gate(
        GateRequest("north_star", output, ctx, requires=("findings[].anchors[].filePath",))
    )
    assert len(diags) == 1
    assert diags[0].path == "findings[0].anchors[1].filePath"


def test_grounded_locations_gate_anchors_output_noop_with_default_locations():
    # Guards against the original bug: default `locations` field names must NOT
    # accidentally read `anchors`, i.e. the derivation is what enables buddies.
    output = {"findings": [{"anchors": [{"filePath": "ghost/b.ts"}]}]}
    ctx = {"changed_files": ["src/a.ts"]}
    assert gates.grounded_locations_gate(GateRequest("north_star", output, ctx)) == []


def test_missing_required_reports_each_field_distinctly():
    """Regression: two missing required fields must render as their OWN names, not
    both collapse to the first entry of the schema's ``required`` list."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["id", "title", "severity", "description"],
    }
    output = {"id": "X"}  # missing title, severity, description
    diags = gates.json_schema_gate(GateRequest("Any", output, {}, schema=schema))
    missing = sorted(d.message.removeprefix("missing required field ").strip("'") for d in diags)
    assert missing == ["description", "severity", "title"]


# ─── composite keywords (oneOf/anyOf) resolve to the concrete branch error ───
_FIX_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fix": {
                        "oneOf": [
                            {
                                "type": "object",
                                "required": ["code"],
                                "properties": {
                                    "code": {"type": "string"},
                                    "language": {"type": "string"},
                                },
                            },
                            {"type": "string"},
                        ]
                    }
                },
            },
        }
    },
}


def test_oneof_surfaces_intended_branch_missing_field():
    """An object failing ``oneOf[object, string]`` must report the object branch's
    missing field, not the opaque "not valid under any of the given schemas" nor the
    string branch's type mismatch."""
    output = {"findings": [{"fix": {"language": "python"}}]}  # object missing `code`
    diags = gates.json_schema_gate(GateRequest("TestQuality", output, {}, schema=_FIX_SCHEMA))
    assert len(diags) == 1
    assert diags[0].path == "findings[0].fix"
    assert diags[0].message == "missing required field 'code'"
    joined = " ".join(d.message for d in diags)
    assert "not valid under any" not in joined


def test_anyof_surfaces_enum_branch():
    """``anyOf[enum-string, null]`` with a bad string surfaces the enum failure."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "verdict_severity": {
                "anyOf": [{"type": "string", "enum": ["low", "high"]}, {"type": "null"}]
            }
        },
    }
    diags = gates.json_schema_gate(
        GateRequest("Judge", {"verdict_severity": "hi"}, {}, schema=schema)
    )
    assert len(diags) == 1
    assert diags[0].path == "verdict_severity"
    assert "not in allowed set" in diags[0].message


def test_anyof_valid_null_passes():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "verdict_severity": {
                "anyOf": [{"type": "string", "enum": ["low", "high"]}, {"type": "null"}]
            }
        },
    }
    assert (
        gates.json_schema_gate(GateRequest("Judge", {"verdict_severity": None}, {}, schema=schema))
        == []
    )


def test_oneof_no_matching_branch_falls_back_to_type():
    """When the instance type matches NO branch, fall back to a concrete leaf error
    rather than the opaque composite message."""
    output = {"findings": [{"fix": 123}]}  # int matches neither object nor string
    diags = gates.json_schema_gate(GateRequest("TestQuality", output, {}, schema=_FIX_SCHEMA))
    assert len(diags) == 1
    assert diags[0].path == "findings[0].fix"
    assert diags[0].message == "expected type object, got integer"


def test_const_keyword_phrasing():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"kind": {"const": "review"}},
    }
    diags = gates.json_schema_gate(GateRequest("Any", {"kind": "audit"}, {}, schema=schema))
    assert len(diags) == 1
    assert diags[0].message == "value 'audit' must equal 'review'"


def _contains_schema(role: str) -> dict:
    """An array that must contain a member tagged ``role``."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "anchors": {
                "type": "array",
                "contains": {"properties": {"role": {"const": role}}, "required": ["role"]},
            }
        },
    }


def test_contains_names_the_missing_tag():
    """A missing tagged member names the tag, instead of echoing the whole array.

    The message is retry feedback the agent reads, so jsonschema's default -- a dump of
    every item that failed to match -- costs an attempt without saying what to add.
    """
    output = {"anchors": [{"role": "defect"}, {"role": "reach"}]}
    diags = gates.json_schema_gate(GateRequest("Any", output, {}, schema=_contains_schema("seed")))
    assert len(diags) == 1
    assert diags[0].path == "anchors"
    assert diags[0].message == "array must contain at least one item matching role='seed'"


def test_contains_satisfied_passes():
    output = {"anchors": [{"role": "seed"}, {"role": "defect"}]}
    assert (
        gates.json_schema_gate(GateRequest("Any", output, {}, schema=_contains_schema("seed")))
        == []
    )


def test_contains_without_pinned_property_falls_back():
    """An unpinned ``contains`` still reads as a shape requirement, not an array dump."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"anchors": {"type": "array", "contains": {"type": "object"}}},
    }
    diags = gates.json_schema_gate(GateRequest("Any", {"anchors": ["x"]}, {}, schema=schema))
    assert len(diags) == 1
    assert diags[0].message == "array must contain at least one item matching the required shape"
