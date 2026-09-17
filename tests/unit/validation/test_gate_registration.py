"""The gate registration seam: a bundle owns its gates, the engine owns the mechanism."""

from __future__ import annotations

import pytest

from roundtable.validation.gate_kit import Diagnostic, GateRequest
from roundtable.validation.gates import SchemaLoadError, register_gate_function


def _noop(request: GateRequest, /) -> list[Diagnostic]:
    return []


def test_duplicate_name_is_rejected():
    """Two bundles claiming one ``fn`` name is a config bug, not last-writer-wins."""
    name = "test_seam_duplicate"
    register_gate_function(name, _noop)

    def _other(request: GateRequest, /) -> list[Diagnostic]:
        return []

    with pytest.raises(SchemaLoadError, match="already registered"):
        register_gate_function(name, _other)


def test_reregistering_the_same_callable_is_idempotent():
    """A module imported twice must not fail — only a genuine collision does."""
    name = "test_seam_idempotent"
    register_gate_function(name, _noop)
    register_gate_function(name, _noop)


def test_engine_gate_name_cannot_be_shadowed():
    with pytest.raises(SchemaLoadError, match="already registered"):
        register_gate_function("json_schema", _noop)
