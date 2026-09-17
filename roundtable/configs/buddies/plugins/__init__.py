"""plugins: the Buddies review bundle's importable domain code.

The generic engine (``context``, ``consolidation``, ``output``, …) holds only
mechanism; the *domain* built-ins that know this config's review shape live here, in
the bundle, and are loaded declaratively — ``agent_graph.yaml`` names
:mod:`.context_plugins` in its top-level ``plugins:`` list, and importing it registers
the ``build_verdict`` output-contract enricher, the ``render_adjudication_inputs``
enricher, and the ``peer_findings`` fan-in extract seam.

Modules:

- :mod:`.context_plugins` — the registration entry point (thin adapters over the rest).
- :mod:`.peer_finding_index` — index the reviewers' outputs into finding records.
- :mod:`.peer_findings` — map that index onto neutral consolidation records.

The dependency arrow points from this bundle *into* the generic core, never out — a
context/output import back into this package fails the doctor boundary guards.
"""
