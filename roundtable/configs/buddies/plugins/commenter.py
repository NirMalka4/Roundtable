"""commenter: how Buddies speaks to a human on the pull request.

Buddies knows two things about a finding that a plain reviewer does not:

* **The claim survived an override attempt.** Every published claim was argued
  against by the Judge and still stood, so the comment leads with what the ruling
  concluded, not with a restatement of the reviewer's alarm.
* **The remediation belongs to a reviewer, not the Judge.** The selected primary
  reviewer payload is rendered exactly and clearly marked unverified because no
  reviewer or Judge executes the proposal.

The engine's :class:`~roundtable.ado.comment_format.DefaultCommenter` cannot say
either of those things, because InspectorX's shape has no adjudication to report.
So Buddies supplies its own renderer through the
:class:`~roundtable.delivery.commenter.Commenter` seam and reuses everything else —
anchoring, dedup, watermarking, the severity floor, posting — untouched.

An escalation asks its question and stops. Nothing reads PR replies today, so an
invitation to "reply and re-run" would be an affordance with nothing behind it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from roundtable.ado import (
    AnchorDecision,
    PublishableFinding,
    normalize_repo_path,
    summary_stable_hash,
    watermark_hashed,
)
from roundtable.ado import clean_markdown as _clean
from roundtable.runtime import get_agent_label

from .claims import ClaimContext, ClaimEvidence
from .configuration import buddies_configuration
from .coverage import ReviewerCoverage

_SUMMARY_ID = "__summary__"


@dataclass(frozen=True)
class _Effect:
    """How one release-effect introduces itself.

    ``label`` heads a thread, where an imperative reads best ("Your call"); ``noun``
    is what the same effect is called when counted ("2 questions"). They differ
    because a heading and a tally are different sentences.
    """

    badge: str
    label: str
    noun: str
    attribution_verb: str


# Ordered as the Judge's own claims array is ordered: blockers, escalations, then
# suggestions.
_EFFECTS: Mapping[str, _Effect] = {
    "blocker": _Effect("🚫", "Blocking", "blocker", "found by"),
    "escalate": _Effect("❓", "Needs decision", "question", "raised by"),
    "suggestion": _Effect("✓", "Non-blocking", "non-blocking", "found by"),
}

_DOMAINS: Mapping[str, tuple[str, str]] = {
    "functional_reliability": ("🧩", "Correctness"),
    "contract_compliance": ("📋", "Contract"),
    "security_privacy": ("🛡️", "Security"),
    "test_evidence": ("🧪", "Tests"),
    "architecture_compatibility": ("🧭", "Architecture"),
    "performance_efficiency": ("⚡", "Performance"),
    "maintainability_quality": ("🧹", "Maintainability"),
    "other": ("💬", "Other"),
}


# The backend derives this vocabulary from claim effects.
_VERDICT_ICONS: Mapping[str, str] = {
    "APPROVE": "✅",
    "APPROVE_WITH_SUGGESTIONS": "⚠️",
    "REJECT": "⛔",
}

# Derived verdict basis — the one bit no individual comment carries:
# whether a REJECT is a proven bug or a gap in the evidence.
_BASIS_GLOSS: Mapping[str, str] = {
    "clean": "every claim was rejected or ruled out of scope",
    "nonblocking_findings": "what stands is worth doing, but none of it holds up the merge",
    "proven_failure": "a claim was upheld and it breaks the release",
    "unresolved_blocker": "something that could block could not be settled from the evidence",
}


def _one_line(value: Any) -> str:
    return " ".join(str(value).split()) if value else ""


class BuddiesCommenter:
    """Render Buddies' adjudicated claims as PR threads plus one verdict comment.

    Built by the ``claims`` projector, which already did the claim→anchor join, so
    this class only decides *wording*: routing and anchoring are settled before it
    is constructed.
    """

    name = "buddies_claims"

    def __init__(
        self,
        payload: Mapping[str, Any],
        *,
        contexts: Mapping[str, ClaimContext],
        anchorless: Sequence[ClaimContext] = (),
        dismissed: int = 0,
        coverage: ReviewerCoverage | None = None,
        session_dir_path: str | None = None,
    ) -> None:
        self._payload = payload
        self._contexts = contexts
        self._anchorless = tuple(anchorless)
        self._dismissed = dismissed
        self._coverage = coverage
        self._session_dir_path = session_dir_path

    # ── one claim, one thread ───────────────────────────────────────────────
    def render_thread(
        self,
        finding: PublishableFinding,
        decision: AnchorDecision,
        *,
        session_id: str,
    ) -> str:
        context = self._contexts.get(finding.id)
        parts = [watermark_hashed(finding.id, finding.stable_hash)]
        if context is None:
            parts.append(f"## {_clean(finding.title) or finding.id}")
        else:
            parts.extend(_header(context))
            parts.extend(_location_note(decision, context))
            parts.extend(self._claim_body(context, inline=decision.kind == "inline"))
        parts.append(_signoff(context))
        return "\n\n".join(p for p in parts if p)

    def _claim_body(self, context: ClaimContext, *, inline: bool) -> list[str]:
        return [
            *_issue(context),
            *_remediation(context),
        ]

    # ── one run, one verdict comment ────────────────────────────────────────
    def render_summary(
        self,
        session_id: str,
        verdict: str,
        findings: Sequence[PublishableFinding],
        *,
        published: Sequence[PublishableFinding] = (),
    ) -> str:
        lines = [
            watermark_hashed(_SUMMARY_ID, summary_stable_hash(session_id, findings)),
            self._ruling_heading(verdict),
            self._judge_intent(),
            self._judge_summary(),
            self._coverage_note(),
            self._accounting(findings, published),
            *self._claim_list("On the diff", self._posted(published), gist=False),
            *self._claim_list(
                "Held back by the severity floor", self._withheld(findings, published), gist=True
            ),
            *self._claim_list("Without a line to attach to", self._anchorless, gist=True),
            f"<sub>{_product()}</sub>",
        ]
        return "\n\n".join(line for line in lines if line)

    def _judge_intent(self) -> str:
        """What the change sets out to do, before anything is said about its quality.

        A reader trusts a ruling more readily once they can see the review understood
        what it was ruling on, so this comes first and stays descriptive.
        """
        return f"_{self._verdict_field('intent')}_" if self._verdict_field("intent") else ""

    def _judge_summary(self) -> str:
        """The Judge's own account of the ruling — the one thing no thread can say.

        Each thread argues a single claim; only this says what the set of them means
        for the merge. So it leads, and everything under it indexes the threads rather
        than restating them — the reader is told each thing exactly once.
        """
        return self._verdict_field("summary")

    def _verdict_field(self, name: str) -> str:
        verdict = self._payload.get("verdict")
        return _clean(verdict.get(name)) if isinstance(verdict, Mapping) else ""

    def _ruling_heading(self, verdict: str) -> str:
        from .verdict import claims_of, derive_label

        icon = _VERDICT_ICONS.get(verdict, "")
        _label, basis = derive_label(claims_of(self._payload))
        gloss = _BASIS_GLOSS.get(basis, "")
        tail = f" — {gloss}" if gloss else ""
        return f"### {icon} {verdict}{tail}".strip()

    def _coverage_note(self) -> str:
        """Which reviewers were missing from the run that produced this verdict.

        Rendered only when something is missing: a PR comment is read by people who
        did not ask for it, so an always-on "all six reported" line would be noise on
        every healthy review. The verdict.md report carries the positive confirmation;
        here silence means complete, and the warning is what has to be impossible to
        miss.
        """
        if self._coverage is None or self._coverage.complete:
            return ""
        reasons = "".join(f"\n> - {reason}" for _name, reason in self._coverage.degraded)
        return f"> ⚠️ **Degraded coverage** — {self._coverage.summary_line()}\n>{reasons}"

    def _accounting(
        self, findings: Sequence[PublishableFinding], published: Sequence[PublishableFinding]
    ) -> str:
        """What was posted, and — just as important — what was not, and why.

        A reader who cannot see the withheld counts cannot tell a quiet review from
        a filtered one, which is exactly what makes a severity floor honest.
        """
        withheld = [
            *_count_phrase(len(findings) - len(published), "held back by the severity floor"),
            *_count_phrase(len(self._anchorless), "not tied to a changed line"),
            *_count_phrase(self._dismissed, "dismissed on review"),
        ]
        head = _posted_line(self._contexts, published)
        return head + ("\n" + " · ".join(withheld) if withheld else "")

    def _withheld(
        self, findings: Sequence[PublishableFinding], published: Sequence[PublishableFinding]
    ) -> list[ClaimContext]:
        """Claims the severity floor kept off the diff — the plan minus what was posted.

        A floor is a display choice, not a judgement: what it hides still belongs in
        the record, or a reader has no way to tell it was ever raised.
        """
        posted = {finding.id for finding in published}
        return [
            self._contexts[finding.id]
            for finding in findings
            if finding.id not in posted and finding.id in self._contexts
        ]

    def _posted(self, published: Sequence[PublishableFinding]) -> list[ClaimContext]:
        """The claims that did get a thread of their own.

        Indexed rather than left implicit: a reader landing on the verdict sees the
        shape of the review — how many, of what kind, and where — without scrolling
        the diff to discover it.
        """
        return [self._contexts[f.id] for f in published if f.id in self._contexts]

    def _claim_list(
        self, heading: str, contexts: Sequence[ClaimContext], *, gist: bool
    ) -> list[str]:
        """One bullet per claim: what it asks, where it lands, and what it is called.

        All three lists share it — what was posted, what the floor held back, and what
        had no line — so a claim looks the same wherever the comment names it. Only a
        claim without a thread of its own adds the ruling in a sentence; for the rest
        that sentence is one click away, and repeating it here would make the index as
        long as the threads it indexes.
        """
        if not contexts:
            return []
        body = [f"#### {heading}"]
        groups: dict[str, list[ClaimContext]] = {}
        for context in contexts:
            groups.setdefault(context.criterion, []).append(context)
        for criterion, grouped in groups.items():
            body.append(f"**{_domain_label(criterion)}**")
            for context in grouped:
                title = _one_line(_clean(context.title)) or context.id
                body.append(
                    f"- **{context.id}** · {context.severity.title()} · "
                    f"{_badge(context)}{_where(context)} — {title}"
                )
                detail = _one_line(_clean(context.gist)) if gist else ""
                if detail and detail != title:
                    body.append(f"  <sub>{detail}</sub>")
        return ["\n".join(body)]


# ─── Thread sections ────────────────────────────────────────────────────────
def _header(context: ClaimContext) -> list[str]:
    effect = _EFFECTS.get(context.effect)
    verb = effect.attribution_verb if effect else "raised by"
    title = _clean(context.title)
    icon, domain = _domain(context.criterion)
    heading = f"## {icon} {title}" if title else f"## {icon} {domain}"
    return [
        heading,
        f"**{domain}** · {context.severity.title()} · {_badge(context)} · "
        f"**{context.id}** · {_attribution(context, verb)}",
    ]


def _attribution(context: ClaimContext, verb: str) -> str:
    """Who found it, and under which of their own finding ids."""
    cited = ", ".join(
        f"{get_agent_label(r.source_agent, buddies_configuration())} ({r.finding_id})"
        for r in context.records
    )
    return f"{verb} {cited}"


def _issue(context: ClaimContext) -> list[str]:
    parts = ["### Issue"]
    gist = _clean(context.gist)
    if gist:
        parts.append(gist)
    parts.extend(_grounding(context))
    return parts


def _grounding(context: ClaimContext) -> list[str]:
    """What a reader opens to confirm the claim — the Judge's citations, and only those.

    Reviewer anchors are deliberately absent. They position the thread and the patch,
    but they are pre-adjudication: they can cover a wider proposition than the one that
    survived, and some mark where a patch applies rather than what was observed. Mixing
    them in produced a list a reader could not check against the claim being made.
    """
    items = [e.rendered for e in context.evidence]
    if not items:
        return []
    return ["**Grounding**\n" + "\n".join(f"- {item}" for item in items)]


def _remediation_narrative(rationale: str | None, proposal: str | None) -> str:
    parts = [_one_line(_clean(value)) for value in (rationale, proposal)]
    return " ".join(part for part in parts if part)


def _remediation(context: ClaimContext) -> list[str]:
    """Render only a remediation draft accepted for this exact claim/finding pair."""
    remediation = context.accepted_remediation
    if remediation is None:
        return []
    parts = ["### Suggested remediation"]
    narrative = _remediation_narrative(remediation.rationale, remediation.proposal)
    if narrative:
        parts.append(narrative)
    illustration = (remediation.illustration or "").strip("\r\n")
    if illustration.strip():
        parts.append(f"```{(remediation.language or '').strip()}\n{illustration}\n```")
    parts.extend(_remediation_support(context))
    if remediation.limitations:
        parts.append(_remediation_limitations(remediation.limitations))
    return parts


def _remediation_support(context: ClaimContext) -> list[str]:
    decision = context.remediation_decision
    if decision is None:
        return []
    return ["**Support**\n" + "\n".join(f"- {item.rendered}" for item in decision.evidence)]


def _remediation_limitations(limitations: Sequence[str]) -> str:
    return "**Known limitations**\n" + "\n".join(f"- {_clean(item)}" for item in limitations)


def _location_note(decision: AnchorDecision, context: ClaimContext) -> list[str]:
    """Where this thread sits, whenever that is not what a reader would assume.

    A comment that wanted a line and did not get one says why. An inline comment
    normally says nothing — but a thread is anchored on a reviewer's *changed* line,
    while the defect a claim describes often lives in code this change never touched,
    which cannot carry a thread at all. When the reader has not landed on anything the
    claim actually cites, say where to look instead of leaving them to guess.
    """
    if decision.kind != "inline":
        return _downgrade_note(decision)
    defect = _defect_elsewhere(decision, context)
    if not defect:
        return []
    return [
        f"<sub>Anchored on the changed line that exposes this; "
        f"the defect itself is at `{defect}`.</sub>"
    ]


def _downgrade_note(decision: AnchorDecision) -> list[str]:
    """Why a comment that wanted a line did not get one."""
    if not decision.file_path:
        return []
    reason = f" — {decision.downgrade_reason}" if decision.downgrade_reason else ""
    return [f"<sub>**Location:** {decision.file_path}{reason}</sub>"]


def _defect_elsewhere(decision: AnchorDecision, context: ClaimContext) -> str:
    """The cited defect's location, when no citation covers the anchored lines."""
    cited = context.evidence
    if not cited or any(_covers(decision, item) for item in cited):
        return ""
    return next((item for item in cited if item.role == "defect"), cited[0]).location


def _covers(decision: AnchorDecision, evidence: ClaimEvidence) -> bool:
    """Whether one citation lands on the lines this thread is anchored to."""
    if normalize_repo_path(decision.file_path) != normalize_repo_path(evidence.file):
        return False
    if decision.start_line is None or evidence.start_line is None:
        return True
    anchor_end = decision.end_line or decision.start_line
    cited_end = evidence.end_line or evidence.start_line
    return evidence.start_line <= anchor_end and decision.start_line <= cited_end


# ─── Verdict-comment helpers ────────────────────────────────────────────────
def _posted_line(
    contexts: Mapping[str, ClaimContext], published: Sequence[PublishableFinding]
) -> str:
    """``**5 suggestions posted**`` — or, when mixed, a bold total plus its breakdown.

    A single-kind breakdown already IS the total, so naming both would say the same
    number twice. Only the headline count is bold; the breakdown reads as detail.
    """
    parts = _breakdown_parts(contexts, published)
    if len(parts) == 1:
        return f"**{parts[0]} posted**"
    total = f"**{len(published)} {_plural('comment', len(published))} posted**"
    return total + (f" · {', '.join(parts)}" if parts else "")


def _breakdown_parts(
    contexts: Mapping[str, ClaimContext], published: Sequence[PublishableFinding]
) -> list[str]:
    """``["2 blockers", "1 question"]`` — what a reader is about to scroll through."""
    counts: dict[str, int] = {}
    for finding in published:
        context = contexts.get(finding.id)
        if context is not None:
            counts[context.effect] = counts.get(context.effect, 0) + 1
    return [
        f"{counts[effect]} {_plural(_EFFECTS[effect].noun, counts[effect])}"
        for effect in _EFFECTS
        if counts.get(effect)
    ]


def _plural(word: str, n: int) -> str:
    return word if n == 1 else word + "s"


def _count_phrase(n: int, tail: str) -> list[str]:
    return [f"{n} {tail}"] if n else []


def _badge(context: ClaimContext) -> str:
    """The release effect as secondary status, not the comment's classification."""
    effect = _EFFECTS.get(context.effect)
    return f"{effect.badge} {effect.label}" if effect else context.effect


def _domain(criterion: str) -> tuple[str, str]:
    return _DOMAINS.get(criterion, _DOMAINS["other"])


def _domain_label(criterion: str) -> str:
    icon, label = _domain(criterion)
    return f"{icon} {label}"


def _where(context: ClaimContext) -> str:
    """`` · `src/a.ts:34` `` — where a folded claim would have been pinned, had it been.

    A claim nobody can locate is a claim nobody can act on, so a fold-in that *has* a
    line says it; one that never had a line (a work item, the PR body) stays silent.
    """
    location = next(iter(context.locations), None)
    if location is None or not location.file_path:
        return ""
    line = f":{location.start_line}" if location.start_line else ""
    return f" · `{location.file_path}{line}`"


def _credit(emoji: str, name: str) -> str:
    return f"{emoji} {name}".strip()


def _product() -> str:
    """``🤝 Buddies`` — the product signing a comment, however the bundle brands itself."""
    configuration = buddies_configuration()
    return _credit(configuration.product_emoji, configuration.product_name)


def _signoff(context: ClaimContext | None) -> str:
    if context is None:
        return f"<sub>{_product()}</sub>"
    return f"<sub>{_product()} · {_reviewer_names(context)}</sub>"


def _reviewer_names(context: ClaimContext) -> str:
    names = [get_agent_label(agent, buddies_configuration()) for agent in context.source_agents]
    return ", ".join(names) if names else "Judge"
