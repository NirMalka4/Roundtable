from __future__ import annotations

import json

import pytest
import yaml
from jsonschema import Draft202012Validator

import roundtable.validation.submission_schema as submission_schema
from roundtable.backend import MockBackend
from roundtable.graph import get_configuration, register_config_plugins
from roundtable.validation import (
    GateSpec,
    SchemaLoadError,
    build_submission_schema,
    evaluate_agent_output,
    evaluate_agent_value,
    load_schema_document,
    run_parsed_pipeline,
)
from roundtable.validation.gates import json_schema_gate
from tests.support.engine import run_agent_with_ovg


def _collect_refs(node, refs):
    if isinstance(node, dict):
        refs.extend(value for key, value in node.items() if key == "$ref")
        for value in node.values():
            _collect_refs(value, refs)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, refs)


@pytest.mark.parametrize("bundle", ["buddies", "inspectorx"])
def test_every_schema_backed_llm_has_closed_tool_schema_accepting_canonical_examples(bundle):
    config = get_configuration(f"roundtable/configs/{bundle}")
    schema_dir = config.root / "schemas"
    covered = 0
    for entry in config.entries:
        if not entry.is_llm or entry.output_schema is None:
            continue
        wrapper = build_submission_schema(entry.output_schema, schema_dir)
        refs = []
        _collect_refs(wrapper, refs)
        assert all(ref.startswith("#/") for ref in refs)
        schema = load_schema_document(entry.output_schema, schema_dir)
        example = (schema.get("examples") or [None])[0]
        assert list(Draft202012Validator(wrapper).iter_errors({"output": example})) == []
        if entry.output_example:
            declared = load_schema_document(entry.output_example, schema_dir)
            assert list(Draft202012Validator(wrapper).iter_errors({"output": declared})) == []
        covered += 1
    assert covered > 0


@pytest.mark.parametrize("bundle", ["buddies", "inspectorx"])
def test_every_canonical_llm_example_passes_the_real_submission_handler(bundle):
    config = get_configuration(f"roundtable/configs/{bundle}")
    register_config_plugins(config)
    backend = MockBackend(config)
    covered = 0
    for entry in config.entries:
        if not entry.is_llm or entry.output_schema is None:
            continue
        outcome = run_agent_with_ovg(
            agent=entry.key,
            context="canonical example probe",
            backend=backend,
            configuration=config,
            max_attempts=1,
        )
        assert outcome.valid, (entry.key, outcome.last_error)
        assert outcome.submission_status == "accepted"
        covered += 1
    assert covered > 0


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("https://example.invalid/schema.json", "remote"),
        ("missing.schema.yaml", "schema not found"),
    ],
)
def test_submission_schema_rejects_remote_and_unresolved_refs(tmp_path, ref, message):
    (tmp_path / "root.yaml").write_text(
        yaml.safe_dump({"type": "object", "properties": {"value": {"$ref": ref}}}),
        encoding="utf-8",
    )
    with pytest.raises(SchemaLoadError, match=message):
        build_submission_schema("root.yaml", tmp_path)


def test_submission_schema_rejects_generated_defs_collision(tmp_path):
    (tmp_path / "external.yaml").write_text("type: string\n", encoding="utf-8")
    (tmp_path / "root.yaml").write_text(
        yaml.safe_dump(
            {
                "$defs": {"__roundtable_external_ZXh0ZXJuYWwueWFtbA": {"type": "integer"}},
                "$ref": "external.yaml",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SchemaLoadError, match="collision"):
        build_submission_schema("root.yaml", tmp_path)


def test_submission_schema_keeps_sanitized_path_aliases_distinct(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.yaml").write_text("type: string\n", encoding="utf-8")
    (tmp_path / "a_b.yaml").write_text("type: integer\n", encoding="utf-8")
    (tmp_path / "root.yaml").write_text(
        yaml.safe_dump(
            {
                "type": "object",
                "required": ["slash", "underscore"],
                "properties": {
                    "slash": {"$ref": "a/b.yaml"},
                    "underscore": {"$ref": "a_b.yaml"},
                },
            }
        ),
        encoding="utf-8",
    )

    wrapper = build_submission_schema("root.yaml", tmp_path)
    properties = wrapper["properties"]["output"]["properties"]
    assert properties["slash"]["$ref"] != properties["underscore"]["$ref"]
    assert (
        list(
            Draft202012Validator(wrapper).iter_errors(
                {"output": {"slash": "owned by a/b.yaml", "underscore": 7}}
            )
        )
        == []
    )


def test_submission_schema_rejects_definition_name_with_different_source_owner(
    tmp_path, monkeypatch
):
    (tmp_path / "first.yaml").write_text("type: string\n", encoding="utf-8")
    (tmp_path / "second.yaml").write_text("type: integer\n", encoding="utf-8")
    (tmp_path / "root.yaml").write_text(
        yaml.safe_dump(
            {
                "allOf": [
                    {"$ref": "first.yaml"},
                    {"$ref": "second.yaml"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(submission_schema, "_definition_name", lambda _path: "forced_collision")

    with pytest.raises(SchemaLoadError, match=r"owned.*first.yaml.*second.yaml"):
        build_submission_schema("root.yaml", tmp_path)


def test_submission_schema_rejects_recursive_external_documents(tmp_path):
    (tmp_path / "a.yaml").write_text('$ref: "b.yaml"\n', encoding="utf-8")
    (tmp_path / "b.yaml").write_text('$ref: "a.yaml"\n', encoding="utf-8")
    with pytest.raises(SchemaLoadError, match="recursive"):
        build_submission_schema("a.yaml", tmp_path)


def test_submission_schema_rejects_unresolved_and_recursive_local_fragments(tmp_path):
    (tmp_path / "missing.yaml").write_text('$ref: "#/$defs/nope"\n', encoding="utf-8")
    with pytest.raises(SchemaLoadError, match="unresolved"):
        build_submission_schema("missing.yaml", tmp_path)

    (tmp_path / "recursive.yaml").write_text(
        yaml.safe_dump(
            {
                "$defs": {"loop": {"$ref": "#/$defs/loop"}},
                "$ref": "#/$defs/loop",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SchemaLoadError, match="recursive"):
        build_submission_schema("recursive.yaml", tmp_path)


@pytest.mark.parametrize(
    ("schema", "path"),
    [
        ({"properties": {"slot": {"$ref": "external.yaml"}}}, ("properties", "slot")),
        (
            {"patternProperties": {"^slot$": {"$ref": "external.yaml"}}},
            ("patternProperties", "^slot$"),
        ),
        ({"$defs": {"slot": {"$ref": "external.yaml"}}}, ("$defs", "slot")),
        (
            {"dependentSchemas": {"slot": {"$ref": "external.yaml"}}},
            ("dependentSchemas", "slot"),
        ),
        ({"items": {"$ref": "external.yaml"}}, ("items",)),
        ({"prefixItems": [{"$ref": "external.yaml"}]}, ("prefixItems", 0)),
        ({"contains": {"$ref": "external.yaml"}}, ("contains",)),
        ({"propertyNames": {"$ref": "external.yaml"}}, ("propertyNames",)),
        ({"additionalProperties": {"$ref": "external.yaml"}}, ("additionalProperties",)),
        ({"contentSchema": {"$ref": "external.yaml"}}, ("contentSchema",)),
        ({"unevaluatedProperties": {"$ref": "external.yaml"}}, ("unevaluatedProperties",)),
        ({"unevaluatedItems": {"$ref": "external.yaml"}}, ("unevaluatedItems",)),
        ({"allOf": [{"$ref": "external.yaml"}]}, ("allOf", 0)),
        ({"anyOf": [{"$ref": "external.yaml"}]}, ("anyOf", 0)),
        ({"oneOf": [{"$ref": "external.yaml"}]}, ("oneOf", 0)),
        ({"not": {"$ref": "external.yaml"}}, ("not",)),
        ({"if": {"$ref": "external.yaml"}}, ("if",)),
        ({"then": {"$ref": "external.yaml"}}, ("then",)),
        ({"else": {"$ref": "external.yaml"}}, ("else",)),
    ],
)
def test_submission_schema_rewrites_refs_in_draft_2020_schema_positions(tmp_path, schema, path):
    (tmp_path / "external.yaml").write_text("type: string\n", encoding="utf-8")
    (tmp_path / "root.yaml").write_text(yaml.safe_dump(schema), encoding="utf-8")

    wrapper = build_submission_schema("root.yaml", tmp_path)
    node = wrapper if path[0] == "$defs" else wrapper["properties"]["output"]
    for segment in path:
        node = node[segment]

    assert node["$ref"].startswith("#/$defs/__roundtable_external_")


def test_submission_schema_preserves_literal_refs_in_instance_valued_keywords(tmp_path):
    (tmp_path / "external.yaml").write_text("type: string\n", encoding="utf-8")
    literals = {
        "const": {"$ref": "external.yaml"},
        "default": {"nested": {"$ref": "external.yaml"}},
        "enum": [{"$ref": "external.yaml"}],
        "examples": [{"$ref": "external.yaml"}],
    }
    (tmp_path / "root.yaml").write_text(yaml.safe_dump(literals), encoding="utf-8")

    output_schema = build_submission_schema("root.yaml", tmp_path)["properties"]["output"]

    assert {key: output_schema[key] for key in literals} == literals


@pytest.mark.parametrize(
    "schema",
    [
        {"$dynamicRef": "https://example.invalid/schema.json"},
        {"$dynamicRef": "external.yaml"},
        {"$dynamicRef": "#slot"},
        {"properties": {"value": {"allOf": [{"$dynamicRef": "#slot"}]}}},
    ],
)
def test_submission_schema_rejects_dynamic_refs_with_location(tmp_path, schema):
    (tmp_path / "root.yaml").write_text(yaml.safe_dump(schema), encoding="utf-8")

    with pytest.raises(SchemaLoadError, match=r"\$dynamicRef.*\$"):
        build_submission_schema("root.yaml", tmp_path)


def test_raw_and_parsed_values_use_the_same_ordered_ovg_pipeline():
    config = get_configuration("roundtable/configs/inspectorx")
    register_config_plugins(config)
    value = load_schema_document("security.schema.yaml", config.root / "schemas")["examples"][0]
    raw = evaluate_agent_output("Security", json.dumps(value), configuration=config)
    parsed = evaluate_agent_value("Security", value, configuration=config)
    assert (
        raw.passed,
        raw.parsed,
        raw.error_messages(),
        raw.warning_messages(),
        raw.failed_gates,
        raw.failed_hints,
    ) == (
        parsed.passed,
        parsed.parsed,
        parsed.error_messages(),
        parsed.warning_messages(),
        parsed.failed_gates,
        parsed.failed_hints,
    )


@pytest.mark.parametrize(
    ("candidate", "root_type"),
    [
        ([], "array"),
        (0, "integer"),
        ("", "string"),
        (False, "boolean"),
        (None, "null"),
    ],
)
def test_non_object_root_returns_diagnostic_when_configured_gate_requires_mapping(
    candidate, root_type
):
    def mapping_gate(request):
        request.output["value"]
        return []

    result = run_parsed_pipeline(
        "root-agent",
        candidate,
        None,
        output_schema={},
        ovg_gates=[{"gate": "mapping"}],
        registry={"mapping": GateSpec("mapping", mapping_gate, "error", None, ())},
    )

    assert result.passed is False
    assert result.failed_gates == ("mapping",)
    assert len(result.errors) == 1
    error = result.errors[0]
    assert (error.path, error.keyword, error.expected, error.actual) == (
        "",
        "type",
        "object",
        root_type,
    )


@pytest.mark.parametrize(
    ("candidate", "root_type"),
    [
        ([], "array"),
        (0, "integer"),
        ("", "string"),
        (False, "boolean"),
        (None, "null"),
    ],
)
def test_schema_and_gates_that_support_non_object_roots_remain_valid(candidate, root_type):
    def root_compatible_gate(_request):
        return []

    registry = {
        "json_schema": GateSpec(
            "json_schema",
            json_schema_gate,
            "error",
            None,
            (),
            accepts_arbitrary_json_root=True,
        ),
        "root_compatible": GateSpec(
            "root_compatible",
            root_compatible_gate,
            "error",
            None,
            (),
            accepts_arbitrary_json_root=True,
        ),
    }
    result = run_parsed_pipeline(
        "root-agent",
        candidate,
        None,
        output_schema={"type": root_type},
        ovg_gates=[{"gate": "json_schema"}, {"gate": "root_compatible"}],
        registry=registry,
    )

    assert result.passed is True
    assert result.parsed == candidate
    assert result.errors == []


def test_arbitrary_root_gate_bugs_are_not_converted_to_root_shape_diagnostics():
    def buggy_gate(_request):
        raise AttributeError("gate implementation bug")

    with pytest.raises(AttributeError, match="gate implementation bug"):
        run_parsed_pipeline(
            "root-agent",
            [],
            None,
            output_schema={"type": "array"},
            ovg_gates=[{"gate": "buggy"}],
            registry={
                "buggy": GateSpec(
                    "buggy",
                    buggy_gate,
                    "error",
                    None,
                    (),
                    accepts_arbitrary_json_root=True,
                )
            },
        )


def test_judge_truncation_reports_exact_location_and_hypothetical_schema_follow_on():
    config = get_configuration("roundtable/configs/buddies")
    register_config_plugins(config)
    prefix = '{"verdict":{"claims":[],"summary":"'
    suffix = '"}'
    raw = prefix + "x" * (15598 - len(prefix) - len(suffix)) + suffix
    result = evaluate_agent_output("Judge", raw, configuration=config)
    messages = "\n".join(result.error_messages())
    assert "EOF at line 1, column 15599, character 15598" in messages
    assert "Unmatched root object opened at line 1, column 1, character 0" in messages
    assert "missing required field 'claims'" in messages
    assert "verdict" in messages and "Additional properties are not allowed ('claims'" in messages
    assert result.passed is False and result.parsed is None
