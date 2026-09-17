"""Fan-out field coherence (Layer 10).

A ``kind: map`` node's ``fan_out.over`` reads a list off a producer's output, so
that field must be an **array** in the producer's ``output_schema`` and the producer
must be a required edge source (so the list is actually delivered). Pinned here:
  * the REAL config is coherent (it has no map nodes yet ⇒ zero errors);
  * an array field on a required-edge producer passes (top-level and nested);
  * a scalar (non-array) field is an error (nothing to fan out over);
  * a producer that is not a required edge source is an error;
  * a producer with no ``output_schema`` is an error.
"""

from __future__ import annotations

from roundtable.bundle import resolve_bundle
from roundtable.graph.model import Edge, FanOut, GraphEntry, get_configuration
from roundtable.validation.coherence import validate_fan_out

_CONFIG = get_configuration(resolve_bundle("inspectorx"))

_SCHEMA = (
    "type: object\n"
    "additionalProperties: false\n"
    "required: [items, name, meta]\n"
    "properties:\n"
    "  items: {type: array, items: {type: string}}\n"
    "  name: {type: string}\n"
    "  meta:\n"
    "    type: object\n"
    "    properties:\n"
    "      files: {type: array, items: {type: string}}\n"
)


def _bundle(tmp_path):
    (tmp_path / "prod.schema.yaml").write_text(_SCHEMA, encoding="utf-8")


def _producer(*, output_schema="prod.schema.yaml"):
    return GraphEntry(
        key="Prod", prompt_path="p.md", edges=(), emoji="x", output_schema=output_schema
    )


def _map(over, *, required=True):
    return GraphEntry(
        key="Fan",
        prompt_path="",
        edges=(Edge(source="Prod", required=required),),
        emoji="x",
        kind="map",
        fan_out=FanOut(over=over),
    )


def test_real_config_has_no_fan_out_errors():
    assert validate_fan_out(_CONFIG.entries, schema_dir=_CONFIG.root / "schemas").errors == []


def test_array_field_on_required_producer_passes(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(), _map("Prod.items")]
    assert validate_fan_out(graph, schema_dir=tmp_path).errors == []


def test_nested_array_field_passes(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(), _map("Prod.meta.files")]
    assert validate_fan_out(graph, schema_dir=tmp_path).errors == []


def test_scalar_field_is_an_error(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(), _map("Prod.name")]
    report = validate_fan_out(graph, schema_dir=tmp_path)
    assert len(report.errors) == 1
    assert "not an array" in report.errors[0]


def test_unknown_field_is_an_error(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(), _map("Prod.nope")]
    report = validate_fan_out(graph, schema_dir=tmp_path)
    assert len(report.errors) == 1
    assert "not an array" in report.errors[0]


def test_producer_not_a_required_edge_is_an_error(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(), _map("Prod.items", required=False)]
    report = validate_fan_out(graph, schema_dir=tmp_path)
    assert len(report.errors) == 1
    assert "not a required edge source" in report.errors[0]


def test_schemaless_producer_is_an_error(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(output_schema=None), _map("Prod.items")]
    report = validate_fan_out(graph, schema_dir=tmp_path)
    assert len(report.errors) == 1
    assert "no output_schema" in report.errors[0]
