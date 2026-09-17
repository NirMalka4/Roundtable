#!/usr/bin/env python3
"""Extract an actionability brief from a completed Buddies session.

The release label and its counts are derived by the reviewing configuration and
persisted in the session. This script reports that decision; it never re-derives one,
because a second implementation of the same rule is free to disagree with the one that
published the comments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _judge_payload(trace: dict[str, Any]) -> dict[str, Any]:
    for agent in trace.get("agents") or []:
        if not isinstance(agent, dict):
            continue
        response = agent.get("response")
        if not isinstance(response, str):
            continue
        try:
            payload = json.loads(response)
        except ValueError:
            continue
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("verdict"), dict)
            and isinstance(payload.get("claims"), list)
        ):
            return payload
    raise ValueError("trace contains no parseable Buddies Judge payload")


#: What the reader should do, keyed by the Judge's own adjudication pair. The pairs are
#: those the reviewing configuration recognizes; a pair outside this map is malformed
#: and is reported as such rather than silently given an action.
ADDRESS_BY_ADJUDICATION: dict[tuple[str, str], str] = {
    ("upheld", "high"): "yes",
    ("upheld", "medium"): "yes",
    ("upheld", "low"): "optional",
    ("insufficient_evidence", "medium"): "investigate",
    ("insufficient_evidence", "low"): "investigate",
    ("rejected", "none"): "no",
    ("not_applicable", "none"): "no",
}


def _adjudication(claim: dict[str, Any]) -> tuple[str, str]:
    return str(claim.get("disposition")), str(claim.get("severity"))


def _address(claim: dict[str, Any]) -> str:
    return ADDRESS_BY_ADJUDICATION.get(_adjudication(claim), "investigate")


def _claim_warnings(claim: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    disposition, severity = _adjudication(claim)
    if (disposition, severity) not in ADDRESS_BY_ADJUDICATION:
        warnings.append(f"unrecognized adjudication {disposition}/{severity}")
    if disposition == "upheld" and not claim.get("evidence"):
        warnings.append("upheld claim has no evidence")
    return warnings


def _claim_brief(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": claim.get("id"),
        "title": claim.get("title"),
        "primarySourceFindingId": claim.get("primary_source_finding_id"),
        "address": _address(claim),
        "disposition": claim.get("disposition"),
        "severity": claim.get("severity"),
        "criterion": claim.get("criterion"),
        "reason": claim.get("reason"),
        "evidence": claim.get("evidence") or [],
        "warnings": _claim_warnings(claim),
    }


def build_brief(session: Path) -> dict[str, Any]:
    trace_path = session.expanduser().resolve() / "trace.json"
    try:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"could not read completed session trace: {error}") from error
    if not isinstance(trace, dict):
        raise ValueError("completed session trace must be a JSON object")
    payload = _judge_payload(trace)
    verdict = payload["verdict"]
    label = trace.get("verdict")
    counts = trace.get("counts")
    if not isinstance(label, str) or not label:
        raise ValueError("completed session trace carries no derived verdict label")
    if not isinstance(counts, dict):
        raise ValueError("completed session trace carries no derived verdict counts")
    claims = [_claim_brief(claim) for claim in payload["claims"] if isinstance(claim, dict)]
    return {
        "session": str(session.expanduser().resolve()),
        "verdict": {
            "label": label,
            "counts": counts,
            "intent": verdict.get("intent"),
            "summary": verdict.get("summary"),
        },
        "claims": claims,
        "warnings": [] if claims else ["Judge adjudicated no claims"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", help="completed Roundtable session directory")
    args = parser.parse_args(argv)
    try:
        brief = build_brief(Path(args.session))
    except ValueError as error:
        print(f"brief_verdict: {error}", file=sys.stderr)
        return 2
    print(json.dumps(brief, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
