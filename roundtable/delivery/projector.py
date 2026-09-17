"""projector: the pluggable publish-plan **projector** seam.

A **projector** is the post-graph adapter that reads a review session's per-agent
results and produces the neutral
:class:`~roundtable.delivery.publishable.PublishableResult` the destination sink
renders. It is the ONE step that knows a given graph topology's output shape — e.g.
Roundtable's ``Judge`` / ``verdict_overlay`` layout vs. a challenger-loop's single
terminal findings list — and maps it onto the shared, topology-agnostic publish
contract. Any topology-specific machinery (Roundtable's count-parity gate, its
operational log line, its finding-count vocabulary) lives INSIDE the projector; the
neutral result exposes only generic fields, so everything downstream (thread
rendering, anchoring, dedup, labels, the sink) is topology-agnostic and reused
as-is.

Concrete projectors live behind this seam and are selected by a config's top-level
``projector:`` key, resolved by :func:`get_projector` — mirroring the sink /
executor / enricher registries. This module is deliberately **topology-agnostic**:
it holds the interface + the lazy registry only, and imports nothing from any
concrete projector (``ado`` etc.), so the seam never couples to one graph's shape.

There is intentionally **no default** here: projection is inherently
topology-specific, so a config that publishes MUST name its projector (the
Roundtable config declares ``projector: verdict_overlay`` in its own
``agent_graph.yaml``). A config with no projector cannot publish — surfaced loudly,
never silently defaulted to some other topology's shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from roundtable.graph import Configuration

    from .publishable import PublishableResult


@runtime_checkable
class Projector(Protocol):
    """Maps a session's per-agent results onto the neutral publish contract.

    ``project`` takes the ``{agent: {response}}`` result map (the whole run
    snapshot — a projector picks whichever nodes it needs; Roundtable resolves
    cross-agent ``verdict_overlay`` refs, a simple topology reads one terminal
    node) plus the optional ``session_dir_path`` used only to stamp the cosmetic
    session id. Returns a :class:`PublishableResult`, or ``None`` when there is
    nothing publishable (e.g. a missing/unparseable terminal output).
    """

    name: str

    def project(
        self, session_results: Mapping[str, Any], *, session_dir_path: str | None = None
    ) -> PublishableResult | None: ...


# ─── Registry ───────────────────────────────────────────────────────────────
# Maps a config's ``projector:`` name to its implementation as a ``"module:attr"``
# dotted path. Dotted (not a class object) so resolution is LAZY — the concrete
# projector (e.g. ``ado.projector``) is imported only when selected, which also
# sidesteps the ``ado`` ↔ ``output`` import cycle. New projectors register one row.
# Projectors registered at import time by a config bundle's ``plugins:`` module
# (the same pattern as ``register_report``), so a bundle-owned projector never
# needs its dotted path written into this engine table. Consulted before the
# static registry above.
_REGISTRY: dict[str, Projector] = {}


def register_projector(name: str, projector: Projector) -> None:
    """Register a bundle-supplied projector under ``name``.

    Raises on a duplicate name — the registry is a static wiring table, so a clash
    is a wiring bug, not something to silently overwrite.
    """
    if name in _REGISTRY:
        raise ValueError(f"projector already registered: {name!r}")
    _REGISTRY[name] = projector


def get_projector(name: str | None) -> Projector:
    """Instantiate the projector a config's ``projector:`` key names.

    ``None`` (no ``projector:`` declared) and an unregistered name are BOTH loud
    config errors naming the known projectors — publishing has no meaningful
    default, so a topology that publishes must declare its own. A bundle-registered
    projector wins; otherwise resolution of the static table is lazy so no concrete
    projector is imported until it is actually selected.
    """
    if name is not None and (registered := _REGISTRY.get(name)) is not None:
        return registered
    if name is None or name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"no projector configured for name {name!r}; declare a registered "
            f"`projector:` in your config. known projectors: {known}"
        )
    return _REGISTRY[name]


def projected_counts(
    session_results: Mapping[str, Any],
    configuration: Configuration | None = None,
) -> dict[str, int]:
    """Domain-result counts sourced from this config's publish projection.

    An **opt-in** strategy, not a default: a bundle's ``build_verdict`` enricher may
    call this when its publish projection is also its natural count vocabulary (the
    projector owns that vocabulary and exposes it as ``counts``). A config that does
    not publish must NOT call it — it would make an optional publish seam a hard
    dependency of the always-run verdict node. Such a config computes its counts
    from its own terminal-agent output instead.

    An empty projection (no/unparseable terminal output) ⇒ all zero.
    """
    from roundtable.extraction import COUNT_KEYS
    from roundtable.graph import get_configuration

    config = configuration or get_configuration()
    projected = get_projector(config.projector).project(session_results)
    if projected is None:
        return dict.fromkeys(COUNT_KEYS, 0)
    return {k: int(projected.counts.get(k, 0)) for k in COUNT_KEYS}
