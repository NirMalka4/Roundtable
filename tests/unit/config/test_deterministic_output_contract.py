"""Generic build-time proof: every ``runtime: deterministic`` producer that declares
an ``output_schema`` emits EXACTLY the top-level fields that schema promises
(``declared == actual``).

Iterate-all IS the enforcement — a new deterministic node with an ``output_schema`` is
covered automatically; if its enricher output drifts from the schema's top-level
property set, this fails. A non-JSON-prose enricher (e.g. DeterministicPreScan) declares
no ``output_schema`` and is skipped. Deterministic nodes are executable at build time, so
unlike LLM nodes (whose emission the runtime OVG gate guards) this proves the contract
statically. Safe because these builders return a fixed key set regardless of input.
"""

from __future__ import annotations

import json

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.context.enrichers import get_enricher
from roundtable.graph import get_configuration, register_config_plugins
from roundtable.utils.json_utils import extract_json
from roundtable.validation.coherence import schema_property_sequences
from roundtable.validation.gates import load_schema_document

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
register_config_plugins(_CONFIG)


def _deterministic_schema_nodes():
    return [e for e in _CONFIG.entries if e.kind == "code" and e.output_schema]


def test_at_least_one_deterministic_schema_node():
    """Guard: if this regresses to zero, the parametrized proof silently covers nothing."""
    assert _deterministic_schema_nodes(), (
        "expected at least one kind=code node declaring an output_schema"
    )


@pytest.mark.parametrize("entry", _deterministic_schema_nodes(), ids=lambda e: e.key)
def test_deterministic_output_matches_declared_schema(entry):
    fn = get_enricher(entry.code_fn)
    assert fn is not None, f"{entry.key}: code_fn {entry.code_fn!r} not registered"

    # Canonical minimal fixture: empty snapshot + empty inputs. These builders emit a
    # fixed key set regardless of input, so the empty fixture still exercises the full
    # top-level contract.
    body = fn({}, {}, entry)
    payload = json.loads(extract_json(body))
    assert isinstance(payload, dict), f"{entry.key}: enricher body is not a JSON object"

    schema_root = _CONFIG.root / "schemas"
    schema = load_schema_document(entry.output_schema, schema_root)
    declared = {seq[0] for seq in schema_property_sequences(schema, schema_root)}
    assert set(payload.keys()) == declared, (
        f"{entry.key}: emitted top-level keys {sorted(payload.keys())} != declared "
        f"output_schema properties {sorted(declared)}"
    )
