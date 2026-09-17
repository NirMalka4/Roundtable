"""Unit tests for the deterministic enricher registry (context.enrichers)."""

from __future__ import annotations

import pytest

import roundtable.context.enrichers as en


@pytest.fixture
def clean_registry():
    """Isolate each test from the process-global enricher registry."""
    saved = dict(en._REGISTRY)
    en._REGISTRY.clear()
    yield en
    en._REGISTRY.clear()
    en._REGISTRY.update(saved)


def test_register_and_get(clean_registry):
    def fn(snapshot, inputs, entry):
        return "body"

    clean_registry.register_enricher("my_fn", fn)
    assert clean_registry.get_enricher("my_fn") is fn
    assert "my_fn" in clean_registry.enricher_names()


def test_get_unregistered_returns_none(clean_registry):
    assert clean_registry.get_enricher("nope") is None


def test_duplicate_registration_raises(clean_registry):
    clean_registry.register_enricher("dup", lambda s, i, e: "")
    with pytest.raises(ValueError, match="already registered"):
        clean_registry.register_enricher("dup", lambda s, i, e: "")


def test_enricher_names_snapshot_is_frozen(clean_registry):
    clean_registry.register_enricher("a", lambda s, i, e: "")
    names = clean_registry.enricher_names()
    assert isinstance(names, frozenset)
    assert names == frozenset({"a"})
