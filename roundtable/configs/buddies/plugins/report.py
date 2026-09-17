"""report: render ``verdict.md`` from THIS config's Judge — the adjudicated claims.

Buddies' Judge emits ``verdict{summary}`` + ``claims[]``; InspectorX's emits a
verdict string + ``verdict_overlay[]``. A report is a *reading* of one of those shapes,
so it belongs in the bundle that owns the shape — the engine holds only the seam
(:mod:`roundtable.delivery.report`). Wired as ``report: claims``.

Two sections, per the agreed layout:

* **Agent understandings** — each reviewer's own ``intent``. Buddies-specific: it is
  what makes a five-perspective review legible (which lens saw what), and it exists
  only because every reviewer schema declares ``intent``.
* **Suggestions** — the Judge's claims, grouped by ``effect`` (the release-impact axis
  its schema declares as *derived*, so grouping on it cannot contradict the verdict)
  and sorted by ``severity`` descending. Severity is the sort AXIS, not a rendered
  field: a reader acts on the group and the evidence, not on a label.

Nothing here is located by node name. The Judge and the reviewers are found by the
SHAPE of their output, so renaming a node in ``agent_graph.yaml`` cannot silently
empty a section.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from roundtable.decision import VerdictResult
from roundtable.delivery import ReportResult
from roundtable.extraction import Remediation
from roundtable.graph import get_product_name
from roundtable.plugins import register_report
from roundtable.runtime import get_agent_display_name

from .claims import ClaimContext, build_contexts, evidence_items
from .coverage import coverage_markdown, reviewer_coverage
from .judge_output import judge_payload as _judge_payload
from .judge_output import parsed_outputs as _parsed_outputs
from .peer_finding_index import build_peer_finding_index
from .remediation_decisions import RemediationDecision, decision_index
from .verdict import claims_of, derive_counts, derive_label

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "judge.schema.yaml"

# Effect -> (heading, lead-in). Ordered blockers, escalations, suggestions — the same
# precedence the Judge schema states for the claims array itself.
_EFFECT_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("blocker", "## 🚫 Blockers", "Upheld and release-blocking — resolve before merge."),
    (
        "escalate",
        "## 🙋 Open questions",
        "The evidence cannot settle these; a human with authority must decide.",
    ),
    (
        "suggestion",
        "## 💡 Suggestions",
        "Non-blocking. Worth doing, but none of these hold up the merge.",
    ),
)

_DISMISSED_EFFECT = "none"


@cache
def _severity_order() -> tuple[str, ...]:
    """The Judge's own ``severity`` enum, most severe first.

    Read from the contract rather than hand-listed here: the schema already declares
    ``[high, medium, low, none]`` in descending order, so the sort and the
    contract are the same list and a future edit to one is an edit to both.
    """
    doc = yaml.safe_load(_SCHEMA_PATH.read_text(encoding="utf-8"))
    claims = doc["properties"]["claims"]["items"]["properties"]
    return tuple(claims["severity"]["enum"])


def _severity_rank(claim: Mapping[str, Any]) -> int:
    """Position in the descending severity scale; unrecognised values sort last."""
    order = _severity_order()
    value = claim.get("severity")
    return order.index(value) if value in order else len(order)


def _sorted_claims(claims: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Severity descending, then claim id ascending so the order is total and stable."""
    return sorted(claims, key=lambda c: (_severity_rank(c), str(c.get("id") or "")))


def _humanize(value: Any) -> str:
    """``insufficient_evidence`` -> ``Insufficient evidence``; a non-enum passes through."""
    return str(value).replace("_", " ").capitalize() if value else ""


def _one_line(value: Any) -> str:
    return " ".join(str(value).split()) if value else ""


def _intent_lines(outputs: Sequence[tuple[str, dict[str, Any]]]) -> list[str]:
    """One line per reviewer that stated an ``intent`` — what that lens took on.

    Distinct from the Judge's own ``verdict.intent``, which states what the change
    does; these state what each reviewer understood itself to be reviewing.
    """
    return [
        f"- **{get_agent_display_name(key)}** — {_one_line(obj['intent'])}"
        for key, obj in outputs
        if isinstance(obj.get("intent"), str) and obj["intent"].strip()
    ]


def _source_refs(claim: Mapping[str, Any]) -> str:
    ids = claim.get("source_finding_ids")
    refs = [f"`{i}`" for i in ids if isinstance(i, str)] if isinstance(ids, list) else []
    return ", ".join(refs)


def _render_evidence(lines: list[str], evidence: Any, label: str) -> None:
    items = evidence_items(evidence)
    if not items:
        return
    lines.append(f"**{label}**")
    lines.append("")
    lines.extend(f"- {_one_line(e.rendered)}" for e in items)
    lines.append("")


def _remediation_narrative(rationale: str | None, proposal: str | None) -> str:
    parts = [_one_line(value) for value in (rationale, proposal)]
    return " ".join(part for part in parts if part)


def _render_remediation_illustration(lines: list[str], remediation: Remediation) -> None:
    lines.extend(
        [
            f"```{(remediation.language or '').strip()}",
            (remediation.illustration or "").strip("\r\n"),
            "```",
            "",
        ]
    )


def _render_withheld_limitations(lines: list[str], limitations: Sequence[str]) -> None:
    if limitations:
        lines.extend(
            [
                "**Known limitations of the withheld proposal**",
                "",
                *[f"- {item}" for item in limitations],
                "",
            ]
        )


def _render_withheld_proposal(lines: list[str], remediation: Remediation) -> None:
    lines.extend(
        [
            "**Reviewer proposal (withheld)**",
            "",
            _remediation_narrative(remediation.rationale, remediation.proposal),
            "",
        ]
    )
    _render_remediation_illustration(lines, remediation)
    _render_withheld_limitations(lines, remediation.limitations)


def _render_scout_rejection(lines: list[str], decision: RemediationDecision) -> None:
    lines.extend(
        [
            "**Why Remedy Scout withheld it**",
            "",
            decision.reason,
            "",
            "**Withholding evidence**",
            "",
            *[f"- {item.rendered}" for item in decision.evidence],
            "",
        ]
    )


def _render_withheld_remediation(lines: list[str], context: ClaimContext) -> None:
    remediation = context.reviewer_remediation
    decision = context.remediation_decision
    source_id = context.claim.get("primary_source_finding_id")
    if remediation is None or decision is None or not isinstance(source_id, str):
        return
    lines.extend(
        [
            f"**Remediation withheld** — `{source_id}`",
            "",
            "_Reviewer-authored proposal withheld by Remedy Scout; not an approved action._",
            "",
        ]
    )
    _render_withheld_proposal(lines, remediation)
    _render_scout_rejection(lines, decision)


def _render_remediation(lines: list[str], context: ClaimContext) -> None:
    remediation = context.accepted_remediation
    decision = context.remediation_decision
    source_id = context.claim.get("primary_source_finding_id")
    if context.reviewer_remediation is None or not isinstance(source_id, str):
        return
    if decision is None:
        lines.extend(
            [
                f"**Remediation withheld** — `{source_id}`",
                "",
                "Remediation adjudication was unavailable or invalid.",
                "",
            ]
        )
        return
    if not decision.publishes or remediation is None:
        _render_withheld_remediation(lines, context)
        return
    lines.extend(
        [
            f"**Suggested remediation** — `{source_id}`",
            "",
            _remediation_narrative(remediation.rationale, remediation.proposal),
            "",
            f"```{(remediation.language or '').strip()}",
            (remediation.illustration or "").strip("\r\n"),
            "```",
            "",
            "**Support**",
            "",
            *[f"- {item.rendered}" for item in decision.evidence],
            "",
        ]
    )
    if remediation.limitations:
        lines.extend(
            [
                "**Known limitations**",
                "",
                *[f"- {item}" for item in remediation.limitations],
                "",
            ]
        )


def _render_claim(lines: list[str], context: ClaimContext) -> None:
    claim = context.claim
    claim_id = claim.get("id") or "J-??"
    criterion = _humanize(claim.get("criterion")) or "Other"
    lines.append(f"### {claim_id} · {criterion}")
    lines.append("")
    lines.append(f"{_source_refs(claim)} · {_humanize(claim.get('disposition')) or 'Unstated'}")
    lines.append("")
    reason = str(claim.get("reason") or "").strip()
    if reason:
        lines.append(reason)
        lines.append("")
    _render_evidence(lines, claim.get("evidence"), "Evidence")
    _render_evidence(lines, claim.get("counter_evidence"), "Counter-evidence")
    _render_remediation(lines, context)


def _render_section(
    lines: list[str],
    heading: str,
    lead: str,
    contexts: Sequence[ClaimContext],
) -> None:
    if not contexts:
        return
    lines.append(heading)
    lines.append("")
    lines.append(f"_{lead}_")
    lines.append("")
    for context in contexts:
        _render_claim(lines, context)


def _render_dismissed(lines: list[str], claims: Sequence[Mapping[str, Any]]) -> None:
    """Rejected / not-applicable claims, collapsed.

    Kept rather than dropped — "this was raised and here is why it was dismissed" is
    the audit trail — but collapsed, because acting on them is not the reader's job.
    """
    if not claims:
        return
    lines.append("<details>")
    lines.append(f"<summary>Dismissed claims ({len(claims)})</summary>")
    lines.append("")
    for claim in claims:
        source = _source_refs(claim)
        disposition = _humanize(claim.get("disposition")) or "Unstated"
        lines.append(
            f"- **{claim.get('id') or 'J-??'}** ({disposition}) {source} — "
            f"{_one_line(claim.get('reason'))}"
        )
    lines.append("")
    lines.append("</details>")
    lines.append("")


def _render_header(
    lines: list[str],
    verdict: VerdictResult,
    counts: Mapping[str, int],
    payload: Mapping[str, Any],
) -> None:
    ruling = payload.get("verdict")
    ruling = ruling if isinstance(ruling, Mapping) else {}
    lines.append(f"# {verdict.verdict_icon} {verdict.verdict}")
    lines.append("")
    _label, derived_basis = derive_label(claims_of(payload))
    basis = _humanize(derived_basis)
    counts_line = (
        f"{counts.get('blocking', 0)} blocking · {counts.get('nonBlocking', 0)} "
        f"non-blocking · {counts.get('security', 0)} security"
    )
    lines.append(f"**{basis}** — {counts_line}" if basis else f"**{counts_line}**")
    lines.append("")
    intent = _one_line(ruling.get("intent"))
    if intent:
        lines.append(f"_{intent}_")
        lines.append("")
    summary = _one_line(ruling.get("summary"))
    if summary:
        lines.append(summary)
        lines.append("")
    if verdict.verdict_overridden:
        lines.append(f"> ⚠️ {verdict.reason}")
        lines.append("")


def _render_subject(lines: list[str], repo_name: str | None, source_branch: str | None) -> None:
    """What was reviewed — a report a reader cannot attribute is not evidence."""
    parts = [p for p in (repo_name, source_branch) if p]
    if not parts:
        return
    lines.append(f"**Reviewed**: {' · '.join(parts)}")
    lines.append("")


def _bucket_by_effect(contexts: Sequence[ClaimContext]) -> dict[str, list[ClaimContext]]:
    """Group claims by their derived ``effect``, which the ruling matrix closes to four values.

    Only OVG-validated output reaches this renderer — an agent that fails its gates
    carries an empty response — so every claim's disposition/severity pair is a legal
    adjudication cell by the time it gets here, and derives one of the four effects. An
    out-of-enum value therefore means that invariant broke, and it is raised rather
    than bucketed away: the caller degrades to the slim verdict stub and says so on
    stderr. Silently dropping the claim is the one thing this report must never do.
    """
    buckets: dict[str, list[ClaimContext]] = {
        effect: [] for effect, _heading, _lead in _EFFECT_SECTIONS
    }
    buckets[_DISMISSED_EFFECT] = []
    for context in contexts:
        effect = context.effect
        if effect not in buckets:
            claim = context.claim
            raise ValueError(
                f"claim {claim.get('id')!r} has disposition {claim.get('disposition')!r} "
                f"with severity {claim.get('severity')!r}, which is not an adjudication "
                f"cell judge.schema.yaml permits — this output cannot have passed OVG"
            )
        buckets[effect].append(context)
    return buckets


class ClaimsReport:
    """``report: claims`` — render ``verdict.md`` from the Judge's adjudicated claims."""

    name = "claims"

    def render(
        self,
        verdict: VerdictResult,
        counts: Mapping[str, int] | None,
        session_results: Mapping[str, Any],
        *,
        repo_name: str | None,
        source_branch: str | None,
        session_id: str,
    ) -> ReportResult | None:
        outputs = _parsed_outputs(session_results)
        payload = _judge_payload(outputs)
        if payload is None:
            return None

        claims = claims_of(payload)
        lines: list[str] = []
        _render_header(lines, verdict, counts or derive_counts(claims), payload)
        _render_subject(lines, repo_name, source_branch)
        lines.extend(coverage_markdown(reviewer_coverage(session_results)))

        intents = _intent_lines(outputs)
        if intents:
            lines.append("## 🧭 Agent understandings")
            lines.append("")
            lines.extend(intents)
            lines.append("")

        index = build_peer_finding_index(session_results)
        contexts = build_contexts(_sorted_claims(claims), index, decision_index(outputs))
        by_effect = _bucket_by_effect(contexts)
        for effect, heading, lead in _EFFECT_SECTIONS:
            _render_section(lines, heading, lead, by_effect[effect])
        _render_dismissed(
            lines,
            [context.claim for context in by_effect[_DISMISSED_EFFECT]],
        )

        lines.append("---")
        lines.append("")
        lines.append(f"*Generated by {get_product_name()} · session `{session_id}`*")
        return ReportResult(markdown="\n".join(lines).rstrip("\n") + "\n")


register_report(ClaimsReport.name, ClaimsReport())
