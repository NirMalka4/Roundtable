"""enrichers: the deterministic-node runtime registry.

A graph node with ``runtime: deterministic`` (agent_graph.yaml) does NOT spawn a
Copilot session. Instead the DAG scheduler invokes a registered pure Python
function over the run snapshot + injection inputs, wraps its string output as an
``AgentRunOutcome`` into the snapshot, and the SAME generic injection path delivers
it to consumers under the node's declared ``delivery_label``. This keeps enrichers
first-class graph nodes (declared deps, declared delivery) with **zero name-matching
special-cases** in ``injection.py`` — the anti-pattern this tier removes.

An enricher's job is to run cheap deterministic computation over the git/diff
context (the kind an LLM should not have to reason about) and hand the agents a
ready HINT, so the LLM nodes spend their budget on judgement, not mechanics.

Contract for ``fn(snapshot, inputs, entry) -> str``:
  * PURE — output derives solely from ``(snapshot, inputs, entry)``; the only inputs
    are the already-collected run snapshot and the git/diff carried on ``inputs``.
  * Returns the section **body** (no heading); the generic delivery path supplies
    the ``delivery_label`` heading, so an enricher never hardcodes its own label.
  * May raise — the scheduler catches it and records an invalid outcome (graceful
    degradation, no retry); an enricher must never crash the run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid import cycles at module load
    from roundtable.engine import AgentRunOutcome
    from roundtable.graph import GraphEntry

# fn(snapshot, source inputs, entry) -> section body (no heading).
EnricherFn = Callable[
    ["Mapping[str, AgentRunOutcome]", "Mapping[str, str]", "GraphEntry"],
    str,
]

_REGISTRY: dict[str, EnricherFn] = {}


def register_enricher(name: str, fn: EnricherFn) -> None:
    """Register a code enricher under ``name`` (the YAML ``code_fn``).

    Raises on a duplicate name — the registry is a static wiring table, so a clash
    is a wiring bug, not something to silently overwrite.
    """
    if name in _REGISTRY:
        raise ValueError(f"enricher already registered: {name!r}")
    _REGISTRY[name] = fn


def get_enricher(name: str) -> EnricherFn | None:
    """Return the registered enricher for ``name``, or ``None`` if unregistered."""
    return _REGISTRY.get(name)


def enricher_names() -> frozenset[str]:
    """All registered enricher names (doctor uses this to validate wiring)."""
    return frozenset(_REGISTRY)


# ── Built-in enricher: the domain-agnostic consolidation reducer ────────────
# Registered at import — the ONE generic enricher the engine core ships. Every
# DOMAIN enricher (pre-scan, security pack, verdict, …) lives in a config's bundle
# plugin instead (declared via agent_graph.yaml ``plugins:`` and registered by
# ``graph.register_config_plugins``), so this module carries no
# review-specific dependency.


def _consolidate_enricher(snapshot, _corpus, entry) -> str:
    """consolidate: the generic fan-in consolidation reducer (any graph, no Python).

    Wires the three domain-agnostic seams of a consolidation from the node's
    declarative ``consolidation:`` block (agent_graph.yaml): resolve the named
    **extract** seam (``context.extractors``) to map this node's valid deps onto
    neutral records, **group** them by gap-based single-linkage under
    ``adjacency_gap``, and **render** the referenceable index + zero-drop count
    checksum with the block's inline render vocabulary. One fn serves every
    consolidation node in every config — Roundtable's ``Dossier_*`` nodes are just
    instances that name the ``specialist_findings`` seam. Returns the section body
    (no heading); the generic delivery path supplies the ``delivery_label``.
    """
    from ..consolidation import build_consolidation, render_consolidation
    from .extractors import get_extractor

    spec = entry.consolidation
    if spec is None:  # doctor forbids this; defensive
        raise ValueError(f"{entry.key}: code_fn=consolidate without a consolidation block")
    extractor = get_extractor(spec.extract)
    if extractor is None:  # doctor forbids this; defensive
        raise ValueError(f"{entry.key}: unregistered extract seam {spec.extract!r}")
    records = extractor(snapshot, entry)
    consolidation = build_consolidation(records, adjacency_gap=spec.adjacency_gap)
    return render_consolidation(consolidation, spec.render)


register_enricher("consolidate", _consolidate_enricher)
