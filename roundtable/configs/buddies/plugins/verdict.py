"""verdict: the Buddies run's domain result, derived from the Judge's own shape.

The engine owns the domain-result ENVELOPE (:mod:`roundtable.extraction.domain_result`);
*deriving* the verdict and its counts belongs here, where this graph's Judge shape is
known. Buddies' Judge emits a ``verdict`` OBJECT plus ``claims[]``; InspectorX's emits
a ``verdict`` STRING plus ``verdict_overlay[]``. Reading either from shared code can
only ever serve one config — the other gets a silent ``UNKNOWN`` with zero counts,
indistinguishable from a Judge that never ran. That is the defect this module closes.

The Judge emits a disposition and a severity per claim, plus an aggregate summary. The
release effect, label, basis, escalation, and counts are all deterministic projections
of ``claims[]`` and therefore never appear in model output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from roundtable.decision import (
    APPROVE,
    APPROVE_WITH_SUGGESTIONS,
    REJECT,
    UNKNOWN,
    VerdictResult,
    get_verdict_icon,
)
from roundtable.extraction import COUNT_KEYS

# One adjudication cell: a disposition, the severities it may carry, and the single
# release effect each pair implies. `insufficient_evidence` cannot reach `high`: an
# unproven consequence must never outrank a proven blocker, either at the publication
# threshold or in the reader's eye.
RULING_MATRIX: dict[str, dict[str, str]] = {
    "upheld": {"high": "blocker", "medium": "suggestion", "low": "suggestion"},
    "insufficient_evidence": {"medium": "escalate", "low": "suggestion"},
    "rejected": {"none": "none"},
    "not_applicable": {"none": "none"},
}


def effect_of(claim: Mapping[str, Any]) -> str:
    """The release effect implied by a claim's disposition and severity.

    The effect is a total function of that pair, so asking the Judge for it only
    created a third value that could disagree with the two it derives from. An
    unrecognized pair returns ``""`` rather than ``"none"``: ``none`` is a real ruling
    meaning "adjudicated away", so reusing it here would report a malformed claim to
    the reader as one the review considered and dismissed. ``""`` reaches no reader
    surface, is counted as malformed in the publish log, and raises in the report.
    """
    severities = RULING_MATRIX.get(str(claim.get("disposition")), {})
    return severities.get(str(claim.get("severity")), "")


# ``effect`` is the release-impact axis, derived above, which is what makes it the safe
# thing to aggregate on. ``escalate`` counts as blocking: the schema pairs it with basis
# ``unresolved_blocker`` and a REJECT, so excluding it would publish a REJECT with zero
# blocking findings — the exact "verdict says one thing, the counts say another"
# confusion this module exists to prevent.
BLOCKING_EFFECTS = frozenset({"blocker", "escalate"})
NON_BLOCKING_EFFECTS = frozenset({"suggestion"})
SECURITY_CRITERION = "security_privacy"

# Deterministic basis values paired with their derived label.
BASIS_CLEAN = "clean"
BASIS_NONBLOCKING = "nonblocking_findings"
BASIS_PROVEN_FAILURE = "proven_failure"
BASIS_UNRESOLVED_BLOCKER = "unresolved_blocker"

NO_CLAIMS_REASON = "Judge adjudicated no claims — nothing substantive was available to decide."
NO_ADJUDICATOR_REASON = "The verdict node has no adjudicating dependency to read."


def claims_of(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The Judge's adjudicated claims, ignoring anything not shaped like one."""
    raw = payload.get("claims")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    return [c for c in raw if isinstance(c, Mapping)]


def derive_label(claims: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """The ``(label, basis)`` the claims imply, per "Aggregation and verdict".

    First match wins, mirroring the precedence the Judge instructions state.
    """
    effects = {effect_of(c) for c in claims}
    if not claims:
        return REJECT, BASIS_UNRESOLVED_BLOCKER
    if "blocker" in effects:
        return REJECT, BASIS_PROVEN_FAILURE
    if "escalate" in effects:
        return REJECT, BASIS_UNRESOLVED_BLOCKER
    if "suggestion" in effects:
        return APPROVE_WITH_SUGGESTIONS, BASIS_NONBLOCKING
    return APPROVE, BASIS_CLEAN


def derive_counts(claims: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Project the claims onto the engine's count vocabulary.

    ``all`` deliberately excludes ``effect: none`` (rejected / not-applicable claims):
    those are adjudicated *away*, and counting them would report findings a reader
    cannot act on.
    """
    blocking = [c for c in claims if effect_of(c) in BLOCKING_EFFECTS]
    non_blocking = [c for c in claims if effect_of(c) in NON_BLOCKING_EFFECTS]
    live = [*blocking, *non_blocking]
    return {
        "blocking": len(blocking),
        "nonBlocking": len(non_blocking),
        "all": len(live),
        "security": len([c for c in live if c.get("criterion") == SECURITY_CRITERION]),
    }


def _summary(payload: Mapping[str, Any]) -> str:
    verdict = payload.get("verdict")
    summary = verdict.get("summary") if isinstance(verdict, Mapping) else None
    return summary if isinstance(summary, str) else ""


def _reason(payload: Mapping[str, Any], claims: Sequence[Mapping[str, Any]]) -> str:
    """The verdict prose: the Judge's own summary, plus the fact of an empty record.

    An empty ``claims`` list is never left to the summary alone — a confident summary
    over zero adjudications is exactly the case a reader must be told about.
    """
    summary = _summary(payload)
    if claims:
        return summary
    return f"{NO_CLAIMS_REASON} {summary}".strip() if summary else NO_CLAIMS_REASON


def derive_verdict(payload: Mapping[str, Any]) -> tuple[VerdictResult, dict[str, int]]:
    """The ``(VerdictResult, counts)`` this Judge payload implies.

    Aggregate metadata is derived from claims; the Judge authors only the summary.
    """
    claims = claims_of(payload)
    label, _basis = derive_label(claims)
    reason = _reason(payload, claims)
    return (
        VerdictResult(
            verdict=label,
            verdict_icon=get_verdict_icon(label),
            verdict_overridden=False,
            reason=reason,
            session_id="",
        ),
        derive_counts(claims),
    )


def _display_name(key: str) -> str:
    """The agent's report name, so a degraded reason reads like the graph, not the code."""
    from roundtable.runtime import get_agent_display_name

    from .configuration import buddies_configuration

    return get_agent_display_name(key, buddies_configuration())


def _derive_or_unknown(response: str, agent: str) -> tuple[VerdictResult, dict[str, int]]:
    """Adjudicate a readable output, or degrade naming the agent that emitted it."""
    import json

    try:
        return derive_verdict(json.loads(response))
    except (ValueError, TypeError, AttributeError) as err:
        return unknown_verdict(f"{agent} produced output that could not be read: {err}")


def _read_judge(snapshot: Mapping[str, Any], entry: Any) -> tuple[VerdictResult, dict[str, int]]:
    """The first dependency that produced readable output; else why none did.

    Every unreadable dependency contributes its own reason, so a degraded verdict
    distinguishes an agent that never ran from one that spent its whole retry budget
    failing validation — the same UNKNOWN, but not the same story.
    """
    from roundtable.result_access import response_of, unreadable_reason

    reasons: list[str] = []
    for key in entry.dep_keys:
        agent = _display_name(key)
        unreadable = unreadable_reason(snapshot.get(key), agent=agent)
        if unreadable is None:
            return _derive_or_unknown(response_of(snapshot[key]) or "", agent)
        reasons.append(unreadable)
    return unknown_verdict(" ".join(reasons) if reasons else NO_ADJUDICATOR_REASON)


def build_verdict_payload(snapshot: Mapping[str, Any], entry: Any) -> str:
    """The sink node's whole job: read this graph's Judge, emit the neutral envelope.

    The Judge is reached through ``entry.dep_keys`` rather than by name, so renaming
    the node in ``agent_graph.yaml`` cannot silently orphan the sink. Tolerant — the
    Judge edge is soft and the sink must always produce a result — but never vague.
    """
    import json

    from roundtable.extraction import build_domain_result

    verdict, counts = _read_judge(snapshot, entry)
    verdict, counts = _apply_coverage(verdict, counts, snapshot)
    return json.dumps(build_domain_result(verdict, counts), sort_keys=True)


def _apply_coverage(
    verdict: VerdictResult, counts: dict[str, int], snapshot: Mapping[str, Any]
) -> tuple[VerdictResult, dict[str, int]]:
    """Fold reviewer coverage into the verdict: annotate a partial roster, block a
    total blackout.

    A partial roster is annotated rather than blocked because the surviving reviewers
    did real work and their findings are real; withholding a verdict over one dead
    reviewer would make a flaky agent able to veto every review. A *total* blackout is
    different in kind, not degree: nothing was reviewed, so any label would be a claim
    about a review that never happened.

    The accepted limitation is that reviewers are fungible here — TaintCheck dying
    counts the same as BigOh dying. Weighting them would encode a security judgment in
    a sink node; naming who is missing puts that judgment on the human instead.
    """
    from .coverage import reviewer_coverage

    coverage = reviewer_coverage(snapshot)
    if coverage.complete:
        return verdict, counts
    if coverage.total_blackout:
        blocked, zero = unknown_verdict(f"{coverage.summary_line()} {verdict.reason}".strip())
        return blocked, zero
    return replace(verdict, reason=f"{verdict.reason} {coverage.summary_line()}".strip()), counts


def unknown_verdict(reason: str) -> tuple[VerdictResult, dict[str, int]]:
    """The degraded result: the Judge edge is soft, so the sink must still emit one."""
    return (
        VerdictResult(
            verdict=UNKNOWN,
            verdict_icon=get_verdict_icon(UNKNOWN),
            verdict_overridden=False,
            reason=reason,
            session_id="",
        ),
        dict.fromkeys(COUNT_KEYS, 0),
    )
