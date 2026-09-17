"""Register the InspectorX verdict-overlay projector."""

from roundtable.plugins import register_projector

from .verdict_overlay import VerdictOverlayProjector

register_projector(VerdictOverlayProjector.name, VerdictOverlayProjector())
