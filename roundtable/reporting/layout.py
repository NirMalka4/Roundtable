"""Layered DAG layout for the agent execution graph.

Pure-Python, dependency-free. Assigns each node to a layer by longest-path from
the roots (so every edge points strictly forward), reduces edge crossings with a
few barycentric ordering sweeps, then hands back grid coordinates. Sized for the
~30-node agent graph — no need for a heavyweight layout engine.

Fan-in bundle collapse: an *aggregator* node (a deterministic reducer ingesting
nearly every producer optionally) would otherwise draw as a hairball. Its
optional inbound edges are folded into a single *bundle* per aggregator; required
edges remain explicit. The full optional producer list is surfaced in the detail
panel instead. Aggregators are detected structurally — a deterministic node with
high optional inbound degree — not by an agent-name convention, so any config
collapses the same. Gating on ``deterministic`` keeps high-fan-in LLM nodes (which
still do real work worth seeing) drawn individually.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from .loader import Edge, ReportModel

_BARYCENTRE_SWEEPS = 4
# A deterministic node is drawn as one collapsed bundle (rather than a fan-in
# hairball) when it ingests a large *share of the whole graph* — an aggregator
# eating nearly every producer. The threshold is a fraction of the node count, so
# it scales to any config size instead of a graph-specific magic count; a small
# floor keeps trivially-tiny graphs from over-collapsing. High-fan-in LLM nodes
# are excluded up front by the deterministic gate.
_FANIN_BUNDLE_FRACTION = 0.25
_FANIN_BUNDLE_FLOOR = 4


@dataclass
class PlacedNode:
    key: str
    layer: int
    order: int  # slot within the layer (post crossing-reduction)


@dataclass
class BundledEdge:
    """A collapsed fan-in: many producers → one aggregator, one glyph."""

    dst: str
    sources: list[str]
    required: bool


@dataclass
class LayoutResult:
    layers: list[list[str]]  # layer index → ordered node keys
    positions: dict[str, PlacedNode]
    edges: list[Edge]  # edges to draw individually (bundle members removed)
    bundles: list[BundledEdge] = field(default_factory=list)


def _fanin_targets(edges: list[Edge], key_set: set[str], aggregator_keys: set[str]) -> set[str]:
    """Aggregator keys whose inbound degree is large enough to draw as a bundle.

    "Large enough" is a fraction of the graph size (floored), so the same rule
    holds whether the config has ten nodes or a hundred. Only optional inputs
    count toward the threshold because required inputs must remain explicit.
    Only ``aggregator_keys`` (deterministic reducers) are eligible; a high-fan-in
    LLM node is intentionally left drawn so its many inputs stay visible.
    """
    threshold = max(_FANIN_BUNDLE_FLOOR, ceil(_FANIN_BUNDLE_FRACTION * len(key_set)))
    indeg: dict[str, int] = {}
    for edge in edges:
        if edge.src in key_set and edge.dst in aggregator_keys and not edge.required:
            indeg[edge.dst] = indeg.get(edge.dst, 0) + 1
    return {k for k, v in indeg.items() if v >= threshold}


def _longest_path_layers(keys: list[str], adj: dict[str, list[str]]) -> dict[str, int]:
    """Assign each node the longest-path depth from any root (cycle-tolerant)."""
    layer: dict[str, int] = dict.fromkeys(keys, 0)
    # Iterate to a fixed point; caps iterations so an accidental cycle can't spin.
    for _ in range(len(keys) + 1):
        changed = False
        for src, dsts in adj.items():
            for dst in dsts:
                want = layer[src] + 1
                if want > layer[dst]:
                    layer[dst] = want
                    changed = True
        if not changed:
            break
    return layer


def compute_layout(model: ReportModel) -> LayoutResult:
    keys = [n.key for n in model.nodes]
    key_set = set(keys)
    aggregator_keys = {n.key for n in model.nodes if n.runtime == "deterministic"}

    # Collapse fan-in aggregators into bundles before layering, so the bundled
    # producers don't distort the layer assignment of the aggregator.
    bundles: list[BundledEdge] = []
    drawn_edges: list[Edge] = []
    bundle_sources: dict[str, list[str]] = {}
    fanin = _fanin_targets(model.edges, key_set, aggregator_keys)
    for edge in model.edges:
        if edge.src not in key_set or edge.dst not in key_set:
            continue
        if edge.dst in fanin and not edge.required:
            bundle_sources.setdefault(edge.dst, []).append(edge.src)
        else:
            drawn_edges.append(edge)
    for dst, sources in bundle_sources.items():
        bundles.append(BundledEdge(dst=dst, sources=sources, required=False))

    # Adjacency for layering uses drawn edges + a single synthetic edge per
    # bundle (from the latest-layer producer) so the aggregator lands after them.
    adj: dict[str, list[str]] = {k: [] for k in keys}
    for edge in drawn_edges:
        adj[edge.src].append(edge.dst)
    layer_of = _longest_path_layers(keys, adj)
    # Push each aggregator just past its deepest producer.
    for dst, sources in bundle_sources.items():
        deepest = max((layer_of[s] for s in sources if s in layer_of), default=layer_of[dst])
        layer_of[dst] = max(layer_of[dst], deepest + 1)
    # And ripple that down to the aggregator's own consumers.
    for _ in range(len(keys) + 1):
        changed = False
        for edge in drawn_edges:
            want = layer_of[edge.src] + 1
            if want > layer_of[edge.dst]:
                layer_of[edge.dst] = want
                changed = True
        if not changed:
            break

    max_layer = max(layer_of.values(), default=0)
    layers: list[list[str]] = [[] for _ in range(max_layer + 1)]
    for key in keys:
        layers[layer_of[key]].append(key)

    _reduce_crossings(layers, drawn_edges, bundle_sources)

    positions: dict[str, PlacedNode] = {}
    for li, layer in enumerate(layers):
        for order, key in enumerate(layer):
            positions[key] = PlacedNode(key=key, layer=li, order=order)

    return LayoutResult(layers=layers, positions=positions, edges=drawn_edges, bundles=bundles)


def _reduce_crossings(
    layers: list[list[str]],
    edges: list[Edge],
    bundle_sources: dict[str, list[str]],
) -> None:
    """Barycentric ordering: repeatedly sort each layer by the mean position of
    its neighbours in the adjacent layer. A handful of sweeps is plenty here."""
    preds: dict[str, list[str]] = {}
    succs: dict[str, list[str]] = {}
    for edge in edges:
        succs.setdefault(edge.src, []).append(edge.dst)
        preds.setdefault(edge.dst, []).append(edge.src)
    # Bundled producers still pull their aggregator horizontally.
    for dst, sources in bundle_sources.items():
        for src in sources:
            succs.setdefault(src, []).append(dst)
            preds.setdefault(dst, []).append(src)

    def order_map() -> dict[str, int]:
        return {k: i for layer in layers for i, k in enumerate(layer)}

    for sweep in range(_BARYCENTRE_SWEEPS):
        pos = order_map()
        downward = sweep % 2 == 0
        indices = range(1, len(layers)) if downward else range(len(layers) - 2, -1, -1)
        neigh = preds if downward else succs
        for li in indices:
            layer = layers[li]

            def bary(key: str, pos=pos, neigh=neigh) -> float:
                ns = [pos[n] for n in neigh.get(key, []) if n in pos]
                return sum(ns) / len(ns) if ns else pos[key]

            layer.sort(key=bary)
            pos = order_map()
