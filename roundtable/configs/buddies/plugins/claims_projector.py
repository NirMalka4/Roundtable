"""claims_projector: project THIS config's adjudicated claims onto the publish contract.

Buddies' Judge emits ``verdict{summary}`` + ``claims[]``; InspectorX's emits a
verdict string + ``verdict_overlay[]``. A projector is a *reading* of one of those
shapes, so it belongs in the bundle that owns the shape — the engine holds only the
seam (:mod:`roundtable.delivery.projector`). Wired as ``projector: claims``.

Routing is decided here, once, so nothing downstream re-litigates it:

* ``effect: none`` — the claim was rejected or ruled out of scope. It is never
  published: a dismissed claim on a PR is noise, and noise is what makes reviewers
  stop reading. It still appears in ``verdict.md``.
* An anchored publishable claim becomes a thread on its line.
* An **anchorless** publishable claim is withheld from ``all_findings`` and folded
  into the verdict comment instead. It would otherwise become a general PR thread
  with no location — and for the claims that land here (a work-item state, a PR
  description or repository-level concern) having no line is the *point*, not a
  failure to find one.

The severity floor is deliberately NOT applied here. ``--min-severity`` is the
publisher's flag and the flow owns it; filtering twice would make it silently
stricter than it says.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from roundtable.ado import PublishableFinding, compute_stable_hash
from roundtable.decision import extract_session_id
from roundtable.delivery import PublishableResult
from roundtable.extraction import NormalizedLocation
from roundtable.plugins import register_projector

from .claims import ClaimContext, build_contexts
from .commenter import BuddiesCommenter
from .coverage import reviewer_coverage
from .judge_output import judge_payload, parsed_outputs
from .peer_finding_index import build_peer_finding_index
from .remediation_decisions import decision_index
from .verdict import claims_of, derive_counts, derive_label


def _finding(context: ClaimContext) -> PublishableFinding:
    """One anchored claim as the neutral finding the publish flow anchors and posts.

    Only the fields the *engine* reads are filled — identity, severity, category and
    location. The comment body is rendered from the claim itself by
    :class:`~roundtable.configs.buddies.plugins.commenter.BuddiesCommenter`, so
    nothing here has to flatten an adjudication into InspectorX's vocabulary.
    """
    locations = context.locations
    primary = locations[0]
    return PublishableFinding(
        id=context.id,
        title=context.title,
        description=context.reason,
        severity=context.severity,
        file_path=primary.file_path,
        start_line=primary.start_line,
        end_line=primary.end_line or primary.start_line,
        location_index=1,
        total_locations=len(locations),
        additional_locations=tuple(locations[1:]),
        stable_hash=_stable_hash(context, locations),
        category="blocking" if context.effect == "blocker" else "non_blocking",
        judge_category=context.criterion,
        source_agents=context.source_agents,
        evidence=tuple(e.rendered for e in context.evidence),
    )


def _stable_hash(context: ClaimContext, locations: Sequence[NormalizedLocation]) -> str:
    """The dedup identity of a published claim: what it says and where it says it.

    Deliberately excludes the session id so re-reviewing the same PR recognizes an
    already-posted comment instead of duplicating it.
    """
    signatures = [f"{loc.file_path}:{loc.start_line}:{loc.end_line}" for loc in locations]
    reviewer_remediation = context.accepted_remediation
    remediation_payload = (
        json.dumps(asdict(reviewer_remediation), sort_keys=True)
        if reviewer_remediation is not None
        else ""
    )
    decision = context.remediation_decision
    decision_payload = ""
    if decision is not None:
        decision_payload = decision.decision
        if decision.publishes:
            decision_payload += json.dumps(
                [asdict(item) for item in decision.evidence],
                sort_keys=True,
            )
    return compute_stable_hash(
        [
            context.id,
            context.severity,
            context.title,
            context.gist,
            *(e.rendered for e in context.evidence),
            str(context.claim.get("primary_source_finding_id") or ""),
            remediation_payload,
            decision_payload,
            *signatures,
        ]
    )


class ClaimsProjector:
    """Project Buddies' adjudicated ``claims[]`` onto a neutral publishable result."""

    name = "claims"

    def project(
        self, session_results: Mapping[str, Any], *, session_dir_path: str | None = None
    ) -> PublishableResult | None:
        outputs = parsed_outputs(session_results)
        payload = judge_payload(outputs)
        if payload is None:
            return None

        contexts = build_contexts(
            claims_of(payload),
            build_peer_finding_index(session_results),
            decision_index(outputs),
        )
        publishable = [c for c in contexts if c.is_publishable]
        anchored = [c for c in publishable if c.locations]
        anchorless = [c for c in publishable if not c.locations]
        findings = [_finding(c) for c in anchored]
        label, _basis = derive_label(claims_of(payload))

        return PublishableResult(
            all_findings=findings,
            session_id=extract_session_id(session_dir_path),
            verdict=label,
            counts=derive_counts(claims_of(payload)),
            log_summary=_log_summary(label, anchored, anchorless, contexts),
            commenter=BuddiesCommenter(
                payload,
                contexts={c.id: c for c in anchored},
                anchorless=anchorless,
                dismissed=sum(1 for c in contexts if c.is_dismissed),
                coverage=reviewer_coverage(session_results),
                session_dir_path=session_dir_path,
            ),
        )


def _log_summary(
    label: str,
    anchored: Sequence[ClaimContext],
    anchorless: Sequence[ClaimContext],
    contexts: Sequence[ClaimContext],
) -> str:
    """The one-line operational summary the sink prints under ``[publish]``.

    ``malformed`` is claims that are neither publishable nor dismissed: their
    disposition/severity pair derives no effect, so they reach no reader surface. Only
    an operator can act on that, which is why it is here and not on the PR.
    """
    dismissed = sum(1 for c in contexts if c.is_dismissed)
    malformed = len(contexts) - len(anchored) - len(anchorless) - dismissed
    return (
        f"plan: verdict={label} | threads={len(anchored)} "
        f"folded-into-verdict={len(anchorless)} "
        f"dismissed={dismissed}" + (f" malformed={malformed}" if malformed else "")
    )


register_projector(ClaimsProjector.name, ClaimsProjector())
