"""context_plugins: the Buddies review bundle's context built-ins.

The generic context registries (:mod:`roundtable.context.enrichers` /
:mod:`roundtable.context.extractors`) hold only the mechanism — the register/resolve
API and the domain-agnostic ``consolidate`` enricher. The *domain* built-ins that know
this config's review shape — the verdict + intents enrichers and the fan-in
extract seam — live here, in the bundle, and register themselves when this module is
imported.

Loading is declarative: ``agent_graph.yaml`` names this module in its top-level
``plugins:`` list, and :func:`roundtable.graph.model.register_config_plugins`
imports it (once) so the registries are populated before doctor-validate or a run
resolves any ``code_fn`` / ``consolidation.extract``. The generic ``context`` package
never imports this module — the dependency arrow points from the bundle *into* the
generic core, never out (proven by the context import-boundary doctor guard).

Every ``register_*`` call takes the function object; the heavy domain imports stay
lazy inside each function so importing this plugin is cheap and cycle-free.
"""

from __future__ import annotations

from roundtable.plugins import register_enricher, register_extractor


def _verdict_enricher(snapshot, _corpus, entry) -> str:
    """Verdict: the output-CONTRACT sink node — assemble the run's domain result.

    Wiring only. The derivation reads THIS graph's Judge shape (``verdict`` object +
    ``claims[]``) and fills the neutral envelope the post-graph presentation layer
    re-hydrates; it lives in :mod:`.verdict` so it is testable without touching the
    enricher registry.
    """
    from .verdict import build_verdict_payload

    return build_verdict_payload(snapshot, entry)


def _peer_findings_extractor(snapshot, entry):
    """Buddies seam: the peer-finding index for one consumer's dep scope.

    Indexes every VALID dependency output (``entry.dep_keys``, soft — a failed reviewer
    degrades the dossier instead of nuking it) and maps each finding onto a neutral record
    (``file`` ⇒ group key, ``line`` ⇒ positional axis; see
    :func:`roundtable.configs.buddies.plugins.peer_findings.findings_to_records`).
    """
    from .peer_finding_index import build_peer_finding_index
    from .peer_findings import findings_to_records

    scope = entry.dep_keys
    response_map = {
        k: {"response": snapshot[k].response} for k in scope if k in snapshot and snapshot[k].valid
    }
    index = build_peer_finding_index(response_map)
    return findings_to_records(index)


def _without_remediation(value):
    """Copy reviewer output while removing remedy content from Judge input."""
    if isinstance(value, dict):
        return {
            key: _without_remediation(item) for key, item in value.items() if key != "remediation"
        }
    if isinstance(value, list):
        return [_without_remediation(item) for item in value]
    return value


def _render_adjudication_inputs(snapshot, _corpus, entry) -> str:
    """Render every reviewer state and claim payload without remediation."""
    import json

    from roundtable.result_access import response_of, unreadable_reason
    from roundtable.runtime import get_agent_display_name

    sections: list[str] = []
    for key in entry.dep_keys:
        name = get_agent_display_name(key)
        outcome = snapshot.get(key)
        response = response_of(outcome)
        if response is None:
            reason = unreadable_reason(outcome, agent=name) or "output is unavailable"
            sections.append(f"### {name} [UNAVAILABLE]\n\n{reason}")
            continue
        try:
            payload = json.loads(response)
        except (ValueError, TypeError):
            sections.append(f"### {name} [UNAVAILABLE]\n\noutput is not valid JSON")
            continue
        sections.append(
            f"### {name} [AVAILABLE]\n\n```json\n"
            f"{json.dumps(_without_remediation(payload), indent=2, ensure_ascii=False)}\n```"
        )
    return "\n\n".join(sections) if sections else "_No reviewer claims in scope._"


def _json_response(outcome):
    import json

    from roundtable.result_access import response_of

    response = response_of(outcome)
    if response is None:
        return None
    try:
        value = json.loads(response)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _reviewer_findings(snapshot, dep_keys):
    from .peer_finding_index import build_index_key

    findings = {}
    for key in dep_keys:
        payload = _json_response(snapshot.get(key))
        rows = payload.get("findings") if payload is not None else None
        if not isinstance(rows, list):
            continue
        for finding in rows:
            if not isinstance(finding, dict):
                continue
            finding_id = finding.get("id")
            if isinstance(finding_id, str):
                findings[build_index_key(key, finding_id)] = finding
    return findings


def _judge_claims(snapshot, dep_keys):
    for key in dep_keys:
        payload = _json_response(snapshot.get(key))
        claims = payload.get("claims") if payload is not None else None
        if isinstance(payload.get("verdict") if payload else None, dict) and isinstance(
            claims, list
        ):
            return claims
    return []


def _remediation_dossier(claim, finding):
    return {
        "claim": {
            key: claim[key]
            for key in (
                "id",
                "title",
                "criterion",
                "disposition",
                "severity",
                "reason",
                "evidence",
            )
            if key in claim
        },
        "primary_source_finding_id": claim["primary_source_finding_id"],
        "reviewer_finding": {
            key: finding[key] for key in ("id", "title", "remediation") if key in finding
        },
    }


def _render_remediation_inputs(snapshot, _corpus, entry) -> str:
    """Join publishable Judge claims to only their selected reviewer remediation."""
    import json

    from .claims import PUBLISHABLE_EFFECTS
    from .verdict import effect_of

    findings = _reviewer_findings(snapshot, entry.dep_keys)
    dossiers = []
    for claim in _judge_claims(snapshot, entry.dep_keys):
        if not isinstance(claim, dict) or effect_of(claim) not in PUBLISHABLE_EFFECTS:
            continue
        selected = claim.get("primary_source_finding_id")
        finding = findings.get(selected)
        if not isinstance(finding, dict) or not isinstance(finding.get("remediation"), dict):
            continue
        dossiers.append(_remediation_dossier(claim, finding))
    return json.dumps({"eligible_remediations": dossiers}, indent=2, ensure_ascii=False)


register_enricher("buddies_build_verdict", _verdict_enricher)
register_enricher("render_adjudication_inputs", _render_adjudication_inputs)
register_enricher("render_remediation_inputs", _render_remediation_inputs)
register_extractor("peer_findings", _peer_findings_extractor)
