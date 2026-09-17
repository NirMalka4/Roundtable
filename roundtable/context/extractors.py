"""extractors: the fan-in *extract-seam* registry for declarative consolidation.

A ``kind: reducer`` (or ``code``) node wired to the generic ``consolidate`` enricher
names an **extract seam** in its ``consolidation:`` block (agent_graph.yaml). That
seam is a registered pure function that maps the node's valid dependency outputs onto
neutral :class:`~roundtable.consolidation.core.Record`s — the ONE domain-specific
step of a consolidation. Everything after it (grouping, the zero-drop partition, the
rendered checksum body) is domain-agnostic engine machinery
(:mod:`roundtable.consolidation`), driven entirely by data the seam puts on each
record (``group_key``/``position``) plus one ``adjacency_gap`` scalar — so a new
consolidation config needs a seam here, not a new reducer fn.

Contract for ``fn(snapshot, entry) -> list[Record]``:
  * PURE — records derive solely from ``entry``'s declared dependency scope
    (``entry.dep_keys``) and those producers' outputs in ``snapshot``.
  * May raise — the reducer node catches it and degrades gracefully (no retry); an
    extractor must never crash the run.

This mirrors :mod:`roundtable.context.enrichers`: a static wiring table holding
only the register/resolve API. The seams themselves are DOMAIN code and live in a
config's bundle plugin (declared via ``agent_graph.yaml`` ``plugins:``), which
registers them before doctor-validate or a run resolves any ``consolidation.extract``
by name — this generic module ships no built-in seam.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid import cycles at module load
    from roundtable.engine import AgentRunOutcome
    from roundtable.graph import GraphEntry

    from ..consolidation import Record

# fn(snapshot, entry) -> the neutral records to consolidate.
ExtractorFn = Callable[["Mapping[str, AgentRunOutcome]", "GraphEntry"], "list[Record]"]

_REGISTRY: dict[str, ExtractorFn] = {}


def register_extractor(name: str, fn: ExtractorFn) -> None:
    """Register an extract seam under ``name`` (the YAML ``consolidation.extract``).

    Raises on a duplicate name — the registry is a static wiring table, so a clash
    is a wiring bug, not something to silently overwrite.
    """
    if name in _REGISTRY:
        raise ValueError(f"extractor already registered: {name!r}")
    _REGISTRY[name] = fn


def get_extractor(name: str) -> ExtractorFn | None:
    """Return the registered extract seam for ``name``, or ``None`` if unregistered."""
    return _REGISTRY.get(name)


def extractor_names() -> frozenset[str]:
    """All registered extract-seam names (doctor uses this to validate wiring)."""
    return frozenset(_REGISTRY)
