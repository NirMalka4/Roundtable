"""domain_result: the neutral verdict/counts ENVELOPE a config's sink node emits.

The output CONTRACT emitted by a config's terminal ``kind: code`` node and
re-hydrated by the post-graph presentation layer. Assembly
(:func:`build_domain_result`) and parsing (:func:`parse_domain_result`) live
together so the payload has ONE definition — the SSOT shared by the producer (a
bundle's ``build_verdict`` enricher) and the reader (``roundtable.review.flow``).

This module owns the envelope and **nothing else**. *How* a verdict and its counts
are derived is the bundle's business: each config's ``build_verdict`` enricher
reads its OWN terminal-agent shape and hands the computed values in. A config whose
Judge emits ``verdict_overlay`` and one whose Judge emits ``claims`` fill the same
envelope without this module knowing either exists.

That separation is load-bearing, not cosmetic: while assembly read the Judge here,
it read exactly ONE config's shape, so the *other* config's perfectly schema-valid
Judge silently produced ``UNKNOWN`` + zero counts — indistinguishable from a Judge
that never ran. Deriving belongs where the shape is known; only the envelope is
shared.

To the execution engine the payload is an opaque string on the sink node's outcome;
only this module knows its shape.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from roundtable.decision import VerdictResult, extract_session_id

# Finding-count keys persisted into trace.json (stable — the report and the
# post-hoc acceptance grader read them). A config projects its own count
# vocabulary onto these keys; a key it has no notion of is simply 0.
COUNT_KEYS = ("blocking", "nonBlocking", "all", "security")


def build_domain_result(verdict: VerdictResult, counts: Mapping[str, int]) -> dict[str, Any]:
    """Assemble the domain-result payload from a bundle's computed verdict + counts.

    Pure assembly — it neither reads a run snapshot nor knows what a Judge is. The
    caller (a bundle's ``build_verdict`` enricher) owns the degradation policy its
    graph needs; the sink node's soft terminal edge means that policy must yield a
    valid result rather than raise.
    """
    return {
        "verdict": verdict.verdict,
        "verdictIcon": verdict.verdict_icon,
        "verdictOverridden": verdict.verdict_overridden,
        "reason": verdict.reason,
        "counts": {k: int(counts.get(k, 0)) for k in COUNT_KEYS},
    }


def parse_domain_result(
    payload: str, *, session_dir_path: str | None = None
) -> tuple[VerdictResult, dict[str, int] | None]:
    """Re-hydrate the ``(VerdictResult, counts)`` pair from a sink node's payload.

    ``session_dir_path`` supplies the cosmetic ``session_id`` (the node has no
    session dir at graph time, so it is stamped here). Raises ``ValueError`` /
    ``KeyError`` on malformed JSON or a missing ``verdict`` — the caller decides how
    to degrade.
    """
    data = json.loads(payload)
    if not isinstance(data, Mapping) or "verdict" not in data:
        raise KeyError("verdict")
    verdict = VerdictResult(
        verdict=data["verdict"],
        verdict_icon=data.get("verdictIcon", ""),
        verdict_overridden=bool(data.get("verdictOverridden", False)),
        reason=data.get("reason", ""),
        session_id=extract_session_id(session_dir_path),
    )
    raw_counts = data.get("counts")
    counts = (
        {k: int(raw_counts.get(k, 0)) for k in COUNT_KEYS}
        if isinstance(raw_counts, Mapping)
        else None
    )
    return verdict, counts
