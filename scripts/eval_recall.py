"""Score whether a completed session recovered a known defect.

Recall is a rate, not a single observation. This reads one finished session and
answers a pre-registered question about it, so repeated replays of the same
frozen input can be counted rather than eyeballed.

The defect being looked for is supplied as data (``--oracle`` or ``--pattern``),
never baked in, so the same instrument scores any regression corpus and no
specific case can leak into the shipped bundle.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RESERVED_NODES = frozenset(
    {
        "ReviewDiff",
        "GitHistory",
        "Consolidation",
        "AdjudicationInputs",
        "Intents",
        "Verdict",
    }
)
JUDGE_NODE = "Judge"

# Verbatim repository text an agent copied in. It reads identically for every agent,
# so matching it measures which file was anchored, not what the agent concluded.
QUOTED_SOURCE_KEYS = frozenset({"excerpt"})

# The argument the agent considered and rejected. Matching it counts a defect the
# finding explicitly argues against as though the finding had asserted it.
REJECTED_ARGUMENT_KEYS = frozenset({"counterevidence"})

IGNORED_KEYS = QUOTED_SOURCE_KEYS | REJECTED_ARGUMENT_KEYS


def _claim_surface(record: object) -> object:
    """The record reduced to what the agent itself asserts."""
    if isinstance(record, dict):
        return {
            key: _claim_surface(value) for key, value in record.items() if key not in IGNORED_KEYS
        }
    if isinstance(record, list):
        return [_claim_surface(item) for item in record]
    return record


class OracleError(ValueError):
    """The supplied oracle cannot be used to score a session."""


@dataclass(frozen=True)
class Oracle:
    """A pre-registered description of the defect a run is expected to surface."""

    id: str
    patterns: tuple[re.Pattern[str], ...]
    description: str = ""
    agents: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, object], *, label: str) -> Oracle:
        raw = payload.get("patterns")
        if not isinstance(raw, list) or not raw:
            raise OracleError(f"{label} must define a non-empty 'patterns' list")
        compiled = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise OracleError(f"{label} patterns must be non-empty strings")
            try:
                compiled.append(re.compile(item, re.IGNORECASE | re.DOTALL))
            except re.error as err:
                raise OracleError(f"{label} pattern {item!r} is not a valid regex: {err}") from err
        agents = payload.get("agents")
        if agents is not None and (
            not isinstance(agents, list) or not all(isinstance(a, str) for a in agents)
        ):
            raise OracleError(f"{label} 'agents' must be a list of agent keys")
        identifier = payload.get("id")
        description = payload.get("description")
        return cls(
            id=identifier if isinstance(identifier, str) and identifier else label,
            patterns=tuple(compiled),
            description=description if isinstance(description, str) else "",
            agents=tuple(agents) if agents else (),
        )

    def covers(self, agent: str) -> bool:
        """Whether findings from this agent count toward the defect."""
        return not self.agents or agent in self.agents

    def matches(self, record: object) -> bool:
        """True when every pattern appears somewhere in the record's own assertions."""
        return self._hits(record) == len(self.patterns)

    def nearly_matches(self, record: object) -> bool:
        """True when all but one pattern appear.

        A claim must carry the whole signature to be counted as found. A dropped
        candidate is only a warning flag, and agents routinely describe a mechanism
        without naming every element of it, so requiring the full signature there
        would hide the very near misses this is meant to surface.
        """
        return self._hits(record) >= max(1, len(self.patterns) - 1)

    def _hits(self, record: object) -> int:
        text = json.dumps(_claim_surface(record), ensure_ascii=False)
        return sum(1 for pattern in self.patterns if pattern.search(text))


@dataclass
class SessionScore:
    """What one session did with the pre-registered defect."""

    session: str
    oracle: str
    outcome: str = "missed"
    reviewer_hits: list[dict[str, object]] = field(default_factory=list)
    judge_hits: list[dict[str, object]] = field(default_factory=list)
    abstention_near_misses: list[dict[str, object]] = field(default_factory=list)
    verdict: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "session": self.session,
            "oracle": self.oracle,
            "outcome": self.outcome,
            "verdict": self.verdict,
            "reviewerHits": self.reviewer_hits,
            "judgeHits": self.judge_hits,
            "abstentionNearMisses": self.abstention_near_misses,
            "notes": self.notes,
        }


def _node_payload(node: object) -> dict[str, object] | None:
    """Reviewer output is a JSON string under 'response'; anything else is not scorable."""
    if not isinstance(node, dict):
        return None
    response = node.get("response")
    if not isinstance(response, str):
        return None
    try:
        decoded = json.loads(response)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _load_results(session: Path) -> dict[str, object]:
    raw = session / "raw_results.json"
    if not raw.is_file():
        raise OracleError(f"{session} is not a complete session: raw_results.json is missing")
    return json.loads(raw.read_text(encoding="utf-8"))


def _score_reviewer(
    agent: str, payload: dict[str, object], oracle: Oracle, score: SessionScore
) -> None:
    for finding in payload.get("findings") or []:
        if not isinstance(finding, dict) or not oracle.matches(finding):
            continue
        score.reviewer_hits.append(
            {
                "agent": agent,
                "findingId": finding.get("id"),
                "severity": finding.get("severity"),
                "title": finding.get("title"),
            }
        )
    for abstention in payload.get("abstentions") or []:
        if not isinstance(abstention, dict) or not oracle.nearly_matches(abstention):
            continue
        score.abstention_near_misses.append(
            {
                "agent": agent,
                "kind": abstention.get("kind"),
                "subject": abstention.get("subject"),
                "hasResolvedArtifact": bool(abstention.get("resolvedArtifact")),
            }
        )


def _claim_sources(claim: dict[str, object]) -> set[str]:
    sources = {item for item in claim.get("source_finding_ids") or [] if isinstance(item, str)}
    primary = claim.get("primary_source_finding_id")
    if isinstance(primary, str):
        sources.add(primary)
    return sources


def _score_judge(payload: dict[str, object], oracle: Oracle, score: SessionScore) -> None:
    """Match a claim by its provenance link first, then by its own wording.

    The Judge restates a finding in its own, deliberately terser words, so matching
    its prose against the oracle under-reports. When a claim names a reviewer finding
    that already matched, the artifact itself says the Judge carried that defect.
    """
    raised = {
        f"{hit['agent']}::{hit['findingId']}"
        for hit in score.reviewer_hits
        if hit.get("agent") and hit.get("findingId")
    }
    for claim in payload.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        linked = sorted(_claim_sources(claim) & raised)
        if not linked and not oracle.matches(claim):
            continue
        score.judge_hits.append(
            {
                "claimId": claim.get("id"),
                "disposition": claim.get("disposition"),
                "effect": claim.get("effect"),
                "severity": claim.get("severity"),
                "matchedBy": "attribution" if linked else "text",
                "sourceFindingIds": claim.get("source_finding_ids"),
            }
        )


def _is_top_grade(hit: dict[str, object]) -> bool:
    """`effect: blocker` was replaced by `severity: high` — the schema calls it a blocker."""
    return hit.get("effect") == "blocker" or (
        hit.get("effect") is None and hit.get("severity") == "high"
    )


def _outcome(score: SessionScore) -> str:
    """Pre-registered grading: a reader only benefits when the Judge publishes it."""
    if any(hit.get("disposition") == "upheld" and _is_top_grade(hit) for hit in score.judge_hits):
        return "published_blocker"
    if score.judge_hits:
        return "published_downgraded"
    if score.reviewer_hits:
        return "raised_not_published"
    return "missed"


def score_session(session: Path, oracle: Oracle) -> SessionScore:
    results = _load_results(session)
    score = SessionScore(session=str(session), oracle=oracle.id)
    payloads = {key: _node_payload(results[key]) for key in sorted(results)}
    # Reviewers first: the Judge is matched against the findings they actually raised.
    for key, payload in payloads.items():
        if payload is None:
            continue
        if key != JUDGE_NODE and key not in RESERVED_NODES and oracle.covers(key):
            _score_reviewer(key, payload, oracle, score)
        if key == "Verdict":
            verdict = payload.get("verdict")
            score.verdict = verdict if isinstance(verdict, str) else None
    judge = payloads.get(JUDGE_NODE)
    if judge is not None:
        _score_judge(judge, oracle, score)
    score.outcome = _outcome(score)
    if score.abstention_near_misses and not score.reviewer_hits:
        score.notes.append(
            "the defect appears only in abstentions: a candidate was considered and dropped"
        )
    return score


def _oracle_from_args(args: argparse.Namespace) -> Oracle:
    if args.oracle and args.pattern:
        raise OracleError("choose either --oracle or --pattern, not both")
    if args.oracle:
        payload = json.loads(args.oracle.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise OracleError(f"{args.oracle} must contain a JSON object")
        return Oracle.from_payload(payload, label=args.oracle.stem)
    if not args.pattern:
        raise OracleError("supply --oracle FILE or at least one --pattern REGEX")
    return Oracle.from_payload({"patterns": args.pattern}, label="inline")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session",
        required=True,
        type=Path,
        nargs="+",
        help="one or more completed session directories to score",
    )
    parser.add_argument("--oracle", type=Path, help="JSON file with 'patterns' and optional 'id'")
    parser.add_argument(
        "--pattern",
        action="append",
        help="regex that must appear in a record; repeat to require several",
    )
    parser.add_argument(
        "--expect",
        help="fail unless every scored session reaches this outcome",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        oracle = _oracle_from_args(args)
        scores = [score_session(session, oracle) for session in args.session]
    except OracleError as err:
        print(f"[eval-recall] {err}", file=sys.stderr)
        return 2
    tally: dict[str, int] = {}
    for score in scores:
        tally[score.outcome] = tally.get(score.outcome, 0) + 1
    print(
        json.dumps(
            {
                "oracle": oracle.id,
                "description": oracle.description,
                "sessions": [score.as_dict() for score in scores],
                "tally": tally,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.expect:
        off = [s.session for s in scores if s.outcome != args.expect]
        if off:
            print(
                f"[eval-recall] {len(off)}/{len(scores)} session(s) did not reach {args.expect!r}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
