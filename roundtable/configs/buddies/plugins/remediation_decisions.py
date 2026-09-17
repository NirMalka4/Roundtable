"""Parse post-Judge remediation publication decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RemediationEvidence:
    source: str
    observation: str
    excerpt: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    component: str | None = None

    @property
    def location(self) -> str:
        if self.source == "draft":
            return f"draft:{self.component}"
        if self.start_line == self.end_line:
            return f"{self.path}:{self.start_line}"
        return f"{self.path}:{self.start_line}-{self.end_line}"

    @property
    def rendered(self) -> str:
        return f"`{self.location}` - {self.observation} (`{self.excerpt}`)"


@dataclass(frozen=True)
class RemediationDecision:
    claim_id: str
    primary_source_finding_id: str
    decision: str
    reason: str
    evidence: tuple[RemediationEvidence, ...]

    @property
    def publishes(self) -> bool:
        return self.decision == "publish"


DecisionKey = tuple[str, str]


def _positive_line(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _evidence_item(value: Any) -> RemediationEvidence | None:
    if not isinstance(value, Mapping):
        return None
    source = value.get("source")
    observation = value.get("observation")
    excerpt = value.get("excerpt")
    if source not in {"repository", "draft"}:
        return None
    if not isinstance(observation, str) or not observation.strip():
        return None
    if not isinstance(excerpt, str) or not excerpt.strip():
        return None
    path = str(value.get("path") or "").strip() or None
    start_line = _positive_line(value.get("start_line"))
    end_line = _positive_line(value.get("end_line"))
    component = str(value.get("component") or "").strip() or None
    if source == "repository" and (
        path is None or start_line is None or end_line is None or end_line < start_line
    ):
        return None
    if source == "draft" and component is None:
        return None
    return RemediationEvidence(
        source=source,
        observation=observation.strip(),
        excerpt=excerpt,
        path=path,
        start_line=start_line,
        end_line=end_line,
        component=component,
    )


def _decision(value: Any) -> RemediationDecision | None:
    if not isinstance(value, Mapping):
        return None
    evidence = value.get("evidence")
    parsed = (
        tuple(item for row in evidence if (item := _evidence_item(row)) is not None)
        if isinstance(evidence, Sequence) and not isinstance(evidence, str | bytes)
        else ()
    )
    claim_id = value.get("claim_id")
    source_id = value.get("primary_source_finding_id")
    outcome = value.get("decision")
    reason = value.get("reason")
    if (
        not isinstance(claim_id, str)
        or not claim_id.strip()
        or not isinstance(source_id, str)
        or not source_id.strip()
        or not isinstance(outcome, str)
        or not outcome.strip()
        or not isinstance(reason, str)
        or not reason.strip()
        or not parsed
    ):
        return None
    if outcome not in {"publish", "withhold"}:
        return None
    if outcome == "publish" and not any(item.source == "repository" for item in parsed):
        return None
    return RemediationDecision(
        claim_id=claim_id.strip(),
        primary_source_finding_id=source_id.strip(),
        decision=outcome,
        reason=reason.strip(),
        evidence=parsed,
    )


def decision_index(
    outputs: Sequence[tuple[str, dict[str, Any]]],
) -> dict[DecisionKey, RemediationDecision]:
    """Return only unambiguous decisions from the Remedy Scout output."""
    rows = next(
        (obj["decisions"] for _key, obj in outputs if isinstance(obj.get("decisions"), list)),
        [],
    )
    grouped: dict[DecisionKey, list[RemediationDecision]] = {}
    for row in rows:
        decision = _decision(row)
        if decision is None:
            continue
        key = (decision.claim_id, decision.primary_source_finding_id)
        grouped.setdefault(key, []).append(decision)
    return {key: values[0] for key, values in grouped.items() if len(values) == 1}
