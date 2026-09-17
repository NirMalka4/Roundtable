"""InspectorX bundle plugin: register this config's session-report renderer.

Pure wiring. The renderer and overlay model live in this bundle.

Imported (once) by :func:`roundtable.graph.model.register_config_plugins`
before ``doctor`` or a run resolves ``report:``.
"""

from __future__ import annotations

from roundtable.plugins import register_report

from .report_renderer import VerdictOverlayReport

register_report("verdict_overlay", VerdictOverlayReport())
