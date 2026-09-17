"""Conditional-edge predicate coherence (Layer 9).

A conditional edge's ``when:`` reads its ``source`` producer's output, so every
leaf field-path must resolve into that producer's ``output_schema``. Pinned here:
  * the REAL config is coherent (it has no conditional edges yet ⇒ zero errors);
  * a reachable field-path passes;
  * an unreachable field-path is an error (silent dead branch);
  * a producer with no ``output_schema`` is an error (nothing to route on);
  * a compound predicate lints every leaf (one bad leaf among good ones fires).
"""

from __future__ import annotations

from roundtable.bundle import resolve_bundle
from roundtable.graph.model import Edge, GraphEntry, get_configuration
from roundtable.graph.predicates import parse_predicate
from roundtable.validation.coherence import validate_conditional_predicates

_CONFIG = get_configuration(resolve_bundle("inspectorx"))

_SCHEMA = (
    "type: object\n"
    "additionalProperties: false\n"
    "required: [risk, meta]\n"
    "properties:\n"
    "  risk: {type: string}\n"
    "  meta:\n"
    "    type: object\n"
    "    properties:\n"
    "      level: {type: string}\n"
)


def _bundle(tmp_path):
    (tmp_path / "prod.schema.yaml").write_text(_SCHEMA, encoding="utf-8")


def _producer(*, output_schema="prod.schema.yaml"):
    return GraphEntry(
        key="Prod", prompt_path="p.md", edges=(), emoji="x", output_schema=output_schema
    )


def _consumer(pred):
    return GraphEntry(
        key="Cons",
        prompt_path="c.md",
        edges=(Edge(source="Prod", required=True, when=parse_predicate(pred)),),
        emoji="x",
    )


def test_real_config_has_no_conditional_predicate_errors():
    assert (
        validate_conditional_predicates(
            _CONFIG.entries,
            schema_dir=_CONFIG.root / "schemas",
        ).errors
        == []
    )


def test_reachable_top_level_field_passes(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(), _consumer({"field": "risk", "equals": "high"})]
    assert validate_conditional_predicates(graph, schema_dir=tmp_path).errors == []


def test_reachable_nested_field_passes(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(), _consumer({"field": "meta.level", "equals": "high"})]
    assert validate_conditional_predicates(graph, schema_dir=tmp_path).errors == []


def test_unreachable_field_is_an_error(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(), _consumer({"field": "nope", "equals": "high"})]
    report = validate_conditional_predicates(graph, schema_dir=tmp_path)
    assert len(report.errors) == 1
    assert "nope" in report.errors[0]


def test_schemaless_producer_is_an_error(tmp_path):
    _bundle(tmp_path)
    graph = [_producer(output_schema=None), _consumer({"field": "risk", "equals": "x"})]
    report = validate_conditional_predicates(graph, schema_dir=tmp_path)
    assert len(report.errors) == 1
    assert "no output_schema" in report.errors[0]


def test_compound_predicate_lints_every_leaf(tmp_path):
    _bundle(tmp_path)
    pred = {"all": [{"field": "risk", "equals": "high"}, {"field": "bad", "in": [1, 2]}]}
    graph = [_producer(), _consumer(pred)]
    report = validate_conditional_predicates(graph, schema_dir=tmp_path)
    assert len(report.errors) == 1  # only the bad leaf
    assert "bad" in report.errors[0]
