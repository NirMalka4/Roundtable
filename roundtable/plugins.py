"""Single typed facade for bundle behavior registration."""

from roundtable.context import (
    EnricherFn,
    ExtractorFn,
    register_enricher,
    register_extractor,
)
from roundtable.delivery import (
    Projector,
    Report,
    register_projector,
    register_report,
)
from roundtable.simulation import (
    SimulationRequest,
    SimulationTransform,
    register_simulation_transform,
)
from roundtable.validation import GateRequest, register_gate_function

__all__ = [
    "EnricherFn",
    "ExtractorFn",
    "GateRequest",
    "Projector",
    "Report",
    "SimulationRequest",
    "SimulationTransform",
    "register_enricher",
    "register_extractor",
    "register_gate_function",
    "register_projector",
    "register_report",
    "register_simulation_transform",
]
