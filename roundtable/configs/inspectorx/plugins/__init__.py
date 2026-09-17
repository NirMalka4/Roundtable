"""plugins: the InspectorX review bundle's importable domain code.

The generic engine (``context``, ``consolidation``, ``persistence``, …) holds only
mechanism; the *domain* built-ins that know this config's review shape live here, in
the bundle, and are loaded declaratively — ``agent_graph.yaml`` names
:mod:`.context_plugins` in its top-level ``plugins:`` list, and importing it registers
the deterministic enrichers (pre-scan, security focus pack, verdict) and the
``specialist_findings`` fan-in extract seam.

Modules:

- :mod:`.context_plugins` — the registration entry point (thin adapters over the rest).
- :mod:`.gates` — this config's OVG gate callables (registered into the generic
  ``validation`` gate registry by name).
- :mod:`.prescan` — the deterministic offline anti-pattern pre-scan.
- :mod:`.security_packs` — ``security_focus_pack`` derivation + SEC intent vocabulary.
- :mod:`.specialist_finding_index` — index specialist outputs into finding records
  (also consumed by :mod:`roundtable.ado.publish` for overlay resolution).
- :mod:`.specialist_findings` — map that index onto neutral consolidation records.

The dependency arrow points from this bundle *into* the generic core, never out — a
context/persistence import back into this package fails the doctor boundary guards.
"""
