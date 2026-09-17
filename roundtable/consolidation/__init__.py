"""consolidation: the domain-neutral, provably-conservative fan-in reducer.

A *consolidation* is a pure, order-independent merge of the records produced within a
graph node's fan-in scope, grouped into cohesive **groups** and stamped with a
**count checksum** — the zero-drop source of truth. It is the engine primitive behind
Roundtable's ``Dossier_*`` nodes, but knows nothing about findings, code review, or any
domain: it operates solely on the neutral :class:`~roundtable.consolidation.core.Record`
and a :class:`~roundtable.consolidation.core.RenderSpec` supplied by the caller.

The lossless, deterministic counterpart to lossy LLM aggregation (Mixture-of-Agents,
Wang et al. 2024; recursive summarization, Wu et al. 2021): where those compress with a
model, this conserves — every input record provably survives, verified by the checksum.
"""

from __future__ import annotations

from .core import (
    Consolidation,
    Group,
    Record,
    RenderSpec,
    build_consolidation,
    render_consolidation,
)

__all__ = [
    "Consolidation",
    "Group",
    "Record",
    "RenderSpec",
    "build_consolidation",
    "render_consolidation",
]
