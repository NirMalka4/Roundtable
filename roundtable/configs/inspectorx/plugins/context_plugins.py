"""context_plugins: the InspectorX review bundle's context built-ins.

The generic context registries (:mod:`roundtable.context.enrichers` /
:mod:`roundtable.context.extractors`) hold only the mechanism — the register/
resolve API and the domain-agnostic ``consolidate`` enricher. The *domain* built-ins
— the deterministic enrichers and the fan-in extract seam that know about this
config's review shape (pre-scan, security focus pack, verdict result, specialist
findings) — live here, in the bundle, and register themselves when this module is
imported.

Loading is declarative: ``agent_graph.yaml`` names this module in its top-level
``plugins:`` list, and :func:`roundtable.graph.model.register_config_plugins`
imports it (once) so the registries are populated before doctor-validate or a run
resolves any ``code_fn`` / ``consolidation.extract``. The generic ``context`` package
never imports this module — the dependency arrow points from the bundle *into* the
generic core, never out (proven by the context import-boundary doctor guard).

Every ``register_*`` call takes the function object; the heavy domain imports stay
lazy inside each function so importing this plugin is cheap and cycle-free — the
imports fire only when the DAG scheduler actually runs the node.
"""

from __future__ import annotations

from roundtable.plugins import register_enricher, register_extractor


def _prescan_enricher(_snapshot, source_inputs, _entry) -> str:
    """DeterministicPreScan: the offline base anti-pattern scan over the review diff.

    Pure over ``corpus.diff`` — the review diff delivered by the ``ReviewDiff``
    ``kind: source`` node, resolved from this node's own source edge. A first-class
    scheduled deterministic node.
    """
    from .prescan import run_prescan

    return run_prescan(source_inputs.get("ReviewDiff", ""))


def _security_focus_pack_enricher(snapshot, source_inputs, _entry) -> str:
    """SecurityFocusPack: the SIP-driven ``security_focus_pack`` scope hint.

    Derives the pack from SecurityIntentProfiler's selection + the static SEC
    catalog + OWASP map + diff keyword evidence (``security_packs``). Its
    ``SecurityIntentProfiler`` hard-dep is guaranteed valid here: G1 marks the node
    invalid (and skips this fn) when the SIP input is degraded, so the consumers
    receive the generic ``## security_focus_pack [REQUIRED — UNAVAILABLE]`` stub
    instead of a misleading all-SKIP pack.
    """
    from .security_packs import build_security_focus_pack, format_security_focus_pack_body

    sip = snapshot.get("SecurityIntentProfiler")
    sip_response = sip.response if sip is not None else ""
    pack = build_security_focus_pack(sip_response, source_inputs.get("ReviewDiff", ""))
    return format_security_focus_pack_body(pack)


def _verdict_enricher(snapshot, _corpus, _entry) -> str:
    """Verdict: the output-CONTRACT sink node — assemble InspectorX's domain result.

    Reads the run snapshot through THIS config's Judge shape (a ``verdict`` string
    plus a ``verdict_overlay`` array) and fills the neutral domain-result envelope the
    post-graph presentation layer re-hydrates. Counts come from this config's publish
    projection, which is also its count vocabulary — an opt-in this bundle makes
    because it publishes; the envelope itself never reaches for a projector.

    Tolerant — a missing/degraded Judge yields UNKNOWN (the node's Judge edge is
    soft), so the sink always produces a valid result. The engine treats the emitted
    string as opaque; only its ENVELOPE is shared
    (:mod:`roundtable.extraction.domain_result`) — the shape it is read FROM is this
    bundle's business.
    """
    import json

    from roundtable.delivery import projected_counts
    from roundtable.extraction import build_domain_result

    from .configuration import inspectorx_configuration
    from .verdict import compute_verdict

    return json.dumps(
        build_domain_result(
            compute_verdict(snapshot, inspectorx_configuration()),
            projected_counts(snapshot, inspectorx_configuration()),
        ),
        sort_keys=True,
    )


def _specialist_findings_extractor(snapshot, entry):
    """InspectorX seam: the specialist-finding index for one consumer's dep scope.

    Indexes every VALID dependency output (``entry.dep_keys``, soft — a failed
    specialist degrades the dossier instead of nuking it) and maps each finding onto
    a neutral record (``file`` ⇒ group key, ``line`` ⇒ positional axis; see
    :func:`roundtable.configs.inspectorx.plugins.specialist_findings.findings_to_records`).
    """
    from .specialist_finding_index import build_specialist_finding_index
    from .specialist_findings import findings_to_records

    scope = entry.dep_keys
    response_map = {
        k: {"response": snapshot[k].response} for k in scope if k in snapshot and snapshot[k].valid
    }
    index = build_specialist_finding_index(response_map)
    return findings_to_records(index)


register_enricher("run_prescan", _prescan_enricher)
register_enricher("build_security_focus_pack", _security_focus_pack_enricher)
register_enricher("inspectorx_build_verdict", _verdict_enricher)
register_extractor("specialist_findings", _specialist_findings_extractor)
