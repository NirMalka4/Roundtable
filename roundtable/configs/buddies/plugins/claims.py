"""claims: an adjudicated claim joined to the reviewer finding it rules on.

The Judge grounds a claim in observations it cites itself, but it never says where the
thread goes — a claim carries no anchor. Reviewer findings are the opposite: every
reviewer schema requires ``anchors`` with at least one entry. Publishing needs both,
and the join between them already exists in the contract:
``claims[].source_finding_ids`` uses exactly the ``{canonical}::{finding_id}`` key
:func:`~roundtable.configs.buddies.plugins.peer_finding_index.build_index_key`
produces.

So a :class:`ClaimContext` is the pairing — the ruling plus the reviewer record that
says where. From it fall the two routing facts publishing needs: whether a claim says
anything to a reader at all (its derived ``effect``), and whether it has a line to say
it on (``locations``).

The two location sources are not interchangeable, and only one of them publishes.
Reviewer anchors position the thread and the patch; they are pre-adjudication, so they
can cover more or less than the claim that survived narrowing. What a reader is shown
to confirm the claim is the claim's own evidence, and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from roundtable.ado import locations_of
from roundtable.extraction import NormalizedLocation, Remediation

from .peer_finding_index import PeerFindingIndex, PeerFindingRecord, build_index_key
from .remediation_decisions import DecisionKey, RemediationDecision
from .verdict import effect_of

# The effects that say something to a human reviewer. `none` is what the Judge
# assigns a claim it rejected or ruled out of scope: adjudicated away, so posting
# it would be noise on the PR, not signal. The report still records it.
PUBLISHABLE_EFFECTS = frozenset({"blocker", "suggestion", "escalate"})

# What each cited observation contributes, said the way a reader would say it. The
# schema's enum is the Judge's vocabulary; this is the reader's.
_ROLE_GLOSS = {
    "defect": "the defect",
    "trigger": "what triggers it",
    "reach": "how it is reached",
    "missing_guard": "the missing guard",
    "contradiction": "what weighs against it",
    "verification": "checked independently",
}


@dataclass(frozen=True)
class ClaimEvidence:
    """One cited observation: where to look, what is true there, and why it counts."""

    file: str
    observation: str
    role: str = ""
    start_line: int | None = None
    end_line: int | None = None

    @property
    def location(self) -> str:
        """``path:line`` or ``path:start-end``; the bare path for a file-level fact."""
        return render_location(self.file, self.start_line, self.end_line)

    @property
    def rendered(self) -> str:
        """The one line a reader sees, identical on the thread and in the report."""
        return render_grounded_observation(
            self.file,
            self.observation,
            self.start_line,
            self.end_line,
            _ROLE_GLOSS.get(self.role),
        )


def render_location(file: str, start_line: int | None, end_line: int | None) -> str:
    if start_line is None:
        return file
    if end_line is None or end_line == start_line:
        return f"{file}:{start_line}"
    return f"{file}:{start_line}-{end_line}"


def render_grounded_observation(
    file: str,
    observation: str,
    start_line: int | None = None,
    end_line: int | None = None,
    qualifier: str | None = None,
) -> str:
    location = render_location(file, start_line, end_line)
    qualification = f" ({qualifier})" if qualifier else ""
    return f"`{location}`{qualification} — {observation}"


@dataclass(frozen=True)
class ClaimContext:
    """One adjudicated claim, joined to the reviewer finding(s) it rules on."""

    claim: Mapping[str, Any]
    records: tuple[PeerFindingRecord, ...] = ()
    remediation_decision: RemediationDecision | None = None

    @property
    def id(self) -> str:
        return str(self.claim.get("id") or "J-??")

    @property
    def effect(self) -> str:
        return effect_of(self.claim)

    @property
    def severity(self) -> str:
        return str(self.claim.get("severity") or "unknown")

    @property
    def criterion(self) -> str:
        return str(self.claim.get("criterion") or "other")

    @property
    def disposition(self) -> str:
        """What the adjudication concluded about the claim."""
        return str(self.claim.get("disposition") or "")

    @property
    def reason(self) -> str:
        return str(self.claim.get("reason") or "").strip()

    @property
    def evidence(self) -> tuple[ClaimEvidence, ...]:
        """The grounding published with this claim."""
        return evidence_items(self.claim.get("evidence"))

    @property
    def counter_evidence(self) -> tuple[ClaimEvidence, ...]:
        """What argued against the resolution. Recorded, never published."""
        return evidence_items(self.claim.get("counter_evidence"))

    @property
    def is_publishable(self) -> bool:
        return self.effect in PUBLISHABLE_EFFECTS

    @property
    def is_dismissed(self) -> bool:
        """Adjudicated away — rejected or ruled out of scope.

        Stated positively on purpose. Defined as "not publishable", it would also
        swallow a claim whose disposition/severity pair derives no effect at all, and
        report that malformed claim to the reader as one the review considered and
        dismissed. That is a claim about the review that never happened.
        """
        return self.effect == "none"

    @property
    def reviewer_remediation(self) -> Remediation | None:
        """The primary reviewer's remediation draft, outside Judge adjudication."""
        record = self.primary_record
        if record is None:
            return None
        remediation = record.finding.remediation
        return remediation if isinstance(remediation, Remediation) else None

    @property
    def primary_record(self) -> PeerFindingRecord | None:
        selected = self.claim.get("primary_source_finding_id")
        if not isinstance(selected, str):
            return None
        return next(
            (
                record
                for record in self.records
                if build_index_key(record.source_agent, record.finding_id) == selected
            ),
            None,
        )

    @property
    def accepted_remediation(self) -> Remediation | None:
        decision = self.remediation_decision
        if decision is None or not decision.publishes:
            return None
        return self.reviewer_remediation

    @property
    def locations(self) -> tuple[NormalizedLocation, ...]:
        """Every anchor the joined reviewer findings carry, primary reviewer first.

        A reviewer claim may still be anchorless when it concerns repository-level
        state rather than a changed line.
        """
        seen: dict[_LocKey, NormalizedLocation] = {}
        records = (
            (self.primary_record, *[r for r in self.records if r is not self.primary_record])
            if self.primary_record is not None
            else self.records
        )
        for record in records:
            for loc in locations_of(record.finding):
                seen.setdefault(_loc_key(loc), loc)
        return tuple(seen.values())

    @property
    def source_agents(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.source_agent for r in self.records))

    @property
    def title(self) -> str:
        return str(self.claim.get("title") or "").strip()

    @property
    def gist(self) -> str:
        return self.reason


_LocKey = tuple[str | None, int | None, int | None]


def _loc_key(loc: NormalizedLocation) -> _LocKey:
    return (loc.file_path, loc.start_line, loc.end_line)


def string_items(raw: Any) -> tuple[str, ...]:
    """The non-empty strings in a list field, or nothing — shared with the commenter."""
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def evidence_items(raw: Any) -> tuple[ClaimEvidence, ...]:
    """The well-formed citations in an evidence field, in the Judge's own order.

    A malformed entry is dropped rather than rendered half-empty: the schema already
    requires a file and an observation, so anything without both is not a citation a
    reader could act on.
    """
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    items = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        file_path = str(entry.get("file") or "").strip()
        observation = str(entry.get("observation") or "").strip()
        if not file_path or not observation:
            continue
        items.append(
            ClaimEvidence(
                file=file_path,
                observation=observation,
                role=str(entry.get("role") or "").strip(),
                start_line=_line(entry.get("start_line")),
                end_line=_line(entry.get("end_line")),
            )
        )
    return tuple(items)


def _line(value: Any) -> int | None:
    """A cited line number, or nothing. ``bool`` is an ``int`` and is not a line."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def build_contexts(
    claims: Sequence[Mapping[str, Any]],
    index: PeerFindingIndex,
    decisions: Mapping[DecisionKey, RemediationDecision] | None = None,
) -> list[ClaimContext]:
    """Join every claim to its cited reviewer findings, in the Judge's own order.

    An id that resolves to nothing is dropped from the join rather than faked: the
    ``claims_coverage`` gate already rules on citation integrity, so publishing
    treats an unresolvable id as "no anchor from this one" and moves on.
    """
    return [
        ClaimContext(
            claim=claim,
            records=_records_for(claim, index),
            remediation_decision=(decisions or {}).get(_decision_key(claim)),
        )
        for claim in claims
        if isinstance(claim, Mapping)
    ]


def _decision_key(claim: Mapping[str, Any]) -> DecisionKey:
    return (
        str(claim.get("id") or ""),
        str(claim.get("primary_source_finding_id") or ""),
    )


def _records_for(
    claim: Mapping[str, Any], index: PeerFindingIndex
) -> tuple[PeerFindingRecord, ...]:
    keys = string_items(claim.get("source_finding_ids"))
    return tuple(record for key in keys if (record := index.by_key.get(key)) is not None)
