"""The Roundtable bundle plugin registers its DOMAIN context built-ins.

The generic ``context.enrichers`` / ``context.extractors`` registries ship no
built-in seam; importing the bundle plugin (declared via ``agent_graph.yaml``
``plugins:`` and loaded by ``register_config_plugins``) is what populates them with
this config's deterministic enrichers and the specialist-finding extract seam.
"""

from __future__ import annotations

import roundtable.configs.inspectorx.plugins.context_plugins  # noqa: F401  (import registers the seams)
from roundtable.bundle import resolve_bundle
from roundtable.context.enrichers import enricher_names, get_enricher
from roundtable.context.extractors import extractor_names, get_extractor
from roundtable.graph.model import get_configuration, register_config_plugins

_CONFIG = get_configuration(resolve_bundle("inspectorx"))


def test_domain_enrichers_registered_by_plugin_import():
    names = enricher_names()
    for name in ("run_prescan", "build_security_focus_pack", "inspectorx_build_verdict"):
        assert name in names
        assert get_enricher(name) is not None


def test_specialist_findings_seam_registered_by_plugin_import():
    assert "specialist_findings" in extractor_names()
    assert get_extractor("specialist_findings") is not None


def test_default_config_declares_the_bundle_plugin():
    assert "roundtable.configs.inspectorx.plugins.context_plugins" in _CONFIG.plugins


def test_register_config_plugins_is_idempotent():
    register_config_plugins(_CONFIG)
    register_config_plugins(_CONFIG)  # second call must not raise a duplicate-registration error
    assert "specialist_findings" in extractor_names()
