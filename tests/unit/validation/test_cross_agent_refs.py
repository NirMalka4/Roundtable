"""Cross-agent output-field reference lint (class-B coupling).

Two behavioural guarantees are pinned here:
  * the REAL config is fully coherent — every ``<delivery_label>.<field>`` reference
    in a shipped prompt resolves to a declared edge on the producing node AND a
    top-level field of that producer's ``output_schema`` (zero errors); and
  * each failure mode actually fires on a synthetic drift — a missing dep edge, an
    unknown producer field, and (for the real security cluster) dropping the real
    hard-dep edge — while a producer's self-reference and a soft-dep edge are exempt.

Synthetic cases use fake entries + a temp bundle so no real prompt is mutated; the
real-config cases derive their mutation from the live graph, so new agents are covered
without editing this file.
"""

from __future__ import annotations

import dataclasses
import types

from roundtable.bundle import resolve_bundle
from roundtable.graph.model import get_configuration
from roundtable.validation.coherence import validate_cross_agent_refs

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
_PROMPT_ROOT = _CONFIG.root / "prompts" / "Reviewer"
_SCHEMA_DIR = _CONFIG.root / "schemas"


def _fake(
    key,
    is_llm,
    *,
    output_schema=None,
    delivery_label=None,
    hard_deps=(),
    soft_deps=(),
    prompt_path=None,
    shared_context=(),
):
    return types.SimpleNamespace(
        key=key,
        is_llm=is_llm,
        output_schema=output_schema,
        delivery_label=delivery_label,
        dep_keys=tuple(hard_deps) + tuple(soft_deps),
        prompt_path=prompt_path,
        shared_context=tuple(shared_context),
    )


_PACK_SCHEMA = (
    "type: object\n"
    "additionalProperties: false\n"
    "required: [good_field, another]\n"
    "properties:\n"
    "  good_field: {type: array}\n"
    "  another: {type: string}\n"
)


def _bundle(tmp_path, prompt_body):
    (tmp_path / "pack.schema.yaml").write_text(_PACK_SCHEMA, encoding="utf-8")
    (tmp_path / "Consumer.agent.md").write_text(prompt_body, encoding="utf-8")


def _producer():
    # is_llm producer → its own schema/example are Layer 5's concern; this lint only
    # needs its label + top-level fields to build the reference registry.
    return _fake(
        "Producer",
        is_llm=True,
        output_schema="pack.schema.yaml",
        delivery_label="## mypack",
        prompt_path=None,
    )


def _consumer(**kw):
    return _fake(
        "Consumer",
        is_llm=True,
        prompt_path="Consumer.agent.md",
        **kw,
    )


# ── the real config is coherent ──────────────────────────────────────────────


def test_real_config_has_no_cross_agent_ref_errors():
    report = validate_cross_agent_refs(
        _CONFIG.entries,
        _PROMPT_ROOT,
        schema_dir=_SCHEMA_DIR,
    )
    assert report.errors == []


# ── (a) a referenced field must be a top-level output_schema property ─────────


def test_unknown_producer_field_is_an_error(tmp_path):
    _bundle(tmp_path, "Use `mypack.good_field` but not `mypack.bad_field`.\n")
    graph = [_producer(), _consumer(hard_deps=("Producer",))]
    report = validate_cross_agent_refs(graph, tmp_path, schema_dir=tmp_path)
    assert len(report.errors) == 1  # only the bad ref, not the valid one
    assert "bad_field" in report.errors[0]
    assert "`mypack.good_field`" not in report.errors[0]  # valid ref stays silent


# ── (b) R3: a reference with no declared edge is an error ─────────────────────


def test_reference_without_declared_edge_is_an_error(tmp_path):
    _bundle(tmp_path, "Restrict scans to `mypack.good_field`.\n")
    graph = [_producer(), _consumer()]  # no hard/soft dep on Producer
    report = validate_cross_agent_refs(graph, tmp_path, schema_dir=tmp_path)
    assert any("edge" in e and "Producer" in e for e in report.errors)


def test_soft_dep_edge_satisfies_the_reference(tmp_path):
    _bundle(tmp_path, "Restrict scans to `mypack.good_field`.\n")
    graph = [_producer(), _consumer(soft_deps=("Producer",))]
    report = validate_cross_agent_refs(graph, tmp_path, schema_dir=tmp_path)
    assert report.errors == []


# ── (F8) a producer referencing its own label is exempt ──────────────────────


def test_producer_self_reference_is_exempt(tmp_path):
    (tmp_path / "pack.schema.yaml").write_text(_PACK_SCHEMA, encoding="utf-8")
    (tmp_path / "Producer.agent.md").write_text(
        "Emit `mypack.good_field` in your output.\n", encoding="utf-8"
    )
    producer = _fake(
        "Producer",
        is_llm=True,
        output_schema="pack.schema.yaml",
        delivery_label="## mypack",
        prompt_path="Producer.agent.md",
    )
    report = validate_cross_agent_refs([producer], tmp_path, schema_dir=tmp_path)
    assert report.errors == []


# ── deterministic producer: schema/example validated here (F5/F10 gap) ────────


def test_deterministic_producer_bad_example_is_an_error(tmp_path):
    (tmp_path / "pack.schema.yaml").write_text(
        _PACK_SCHEMA + "examples:\n  - {good_field: 1, another: x}\n", encoding="utf-8"
    )
    (tmp_path / "Consumer.agent.md").write_text("no refs here\n", encoding="utf-8")
    # is_llm=False → Layer 5 (validate_ovg_coherence) skips it, so THIS lint owns the
    # schema/example self-validation for deterministic producers.
    det = _fake(
        "Det",
        is_llm=False,
        output_schema="pack.schema.yaml",
        delivery_label="## mypack",
    )
    report = validate_cross_agent_refs([det], tmp_path, schema_dir=tmp_path)
    assert any("examples[0]" in e and "Det" in e for e in report.errors)


# ── the reference is scanned in shared_context files too ─────────────────────


def test_reference_in_shared_context_file_is_linted(tmp_path):
    (tmp_path / "pack.schema.yaml").write_text(_PACK_SCHEMA, encoding="utf-8")
    (tmp_path / "Consumer.agent.md").write_text("body\n", encoding="utf-8")
    (tmp_path / "Shared.md").write_text("Refer to `mypack.bad_field`.\n", encoding="utf-8")
    graph = [
        _producer(),
        _consumer(hard_deps=("Producer",), shared_context=("Shared.md",)),
    ]
    report = validate_cross_agent_refs(graph, tmp_path, schema_dir=tmp_path)
    assert any("bad_field" in e and "Shared.md" in e for e in report.errors)


# ── real security cluster: dropping the real edge makes the lint fire ─────────


def test_dropping_real_security_focus_pack_edge_fires():
    """AttackSurfaceScanner references ``security_focus_pack.<field>`` and hard-deps
    its producer; removing that edge must turn the lint red (proves it guards the
    real class-B coupling, not just synthetic fixtures)."""
    consumer = next(e for e in _CONFIG.entries if e.key == "AttackSurfaceScanner")
    assert "SecurityFocusPack" in consumer.required_dep_keys  # precondition
    stripped = dataclasses.replace(
        consumer, edges=tuple(ed for ed in consumer.edges if ed.source != "SecurityFocusPack")
    )
    graph = tuple(stripped if e.key == consumer.key else e for e in _CONFIG.entries)
    report = validate_cross_agent_refs(graph, _PROMPT_ROOT, schema_dir=_SCHEMA_DIR)
    assert any("security_focus_pack" in e and "AttackSurfaceScanner" in e for e in report.errors)
