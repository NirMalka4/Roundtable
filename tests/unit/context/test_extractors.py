"""Unit tests for the fan-in extract-seam registry (context.extractors).

The registry is generic mechanism only — the Roundtable ``specialist_findings``
seam is registered by the bundle plugin, covered in
``tests/unit/configs/inspectorx/plugins/test_context_plugins.py``.
"""

from __future__ import annotations

import pytest

import roundtable.context.extractors as ex


@pytest.fixture
def clean_registry():
    """Isolate each test from the process-global extractor registry."""
    saved = dict(ex._REGISTRY)
    ex._REGISTRY.clear()
    yield ex
    ex._REGISTRY.clear()
    ex._REGISTRY.update(saved)


def test_register_and_get(clean_registry):
    def fn(snapshot, entry):
        return []

    clean_registry.register_extractor("my_seam", fn)
    assert clean_registry.get_extractor("my_seam") is fn
    assert "my_seam" in clean_registry.extractor_names()


def test_get_unregistered_returns_none(clean_registry):
    assert clean_registry.get_extractor("nope") is None


def test_duplicate_registration_raises(clean_registry):
    clean_registry.register_extractor("dup", lambda s, e: [])
    with pytest.raises(ValueError, match="already registered"):
        clean_registry.register_extractor("dup", lambda s, e: [])


def test_enricher_names_snapshot_is_frozen(clean_registry):
    clean_registry.register_extractor("a", lambda s, e: [])
    names = clean_registry.extractor_names()
    assert isinstance(names, frozenset)
    assert names == frozenset({"a"})
