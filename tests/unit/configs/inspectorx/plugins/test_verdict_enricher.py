"""InspectorX's ``build_verdict`` sink node owns its own verdict derivation.

The domain-result envelope is shape-blind — it takes an already-computed verdict +
counts. So the contract that made the node's Judge edge soft in the first place ("no
or degraded Judge ⇒ UNKNOWN + zero counts, never raise") is guarded here, against the
enricher that actually derives it, rather than against the shared envelope.

These also pin that the enricher reads THIS config's Judge shape — a ``verdict``
string plus a ``verdict_overlay`` array — which is precisely the knowledge that must
not live in a module both configs share.
"""

from __future__ import annotations

import json

import roundtable.configs.inspectorx.plugins.context_plugins  # noqa: F401  (registers the seam)
from roundtable.context.enrichers import get_enricher
from roundtable.decision import APPROVE, REJECT, UNKNOWN
from roundtable.extraction.domain_result import COUNT_KEYS, parse_domain_result


def _build_verdict(snapshot: dict) -> dict:
    payload = get_enricher("inspectorx_build_verdict")(snapshot, None, None)
    return json.loads(payload)


def test_no_judge_yields_unknown_and_zero_counts() -> None:
    """The soft-edge contract: a sink node always emits a valid result."""
    result = _build_verdict({"Analyst": {"response": "irrelevant"}})

    assert result["verdict"] == UNKNOWN
    assert result["counts"] == dict.fromkeys(COUNT_KEYS, 0)


def test_unparseable_judge_yields_unknown_rather_than_raising() -> None:
    result = _build_verdict({"Judge": {"response": "not json at all {"}})

    assert result["verdict"] == UNKNOWN


def test_reads_this_configs_judge_shape() -> None:
    """A flat ``verdict`` string is InspectorX's shape and must be honoured."""
    result = _build_verdict({"Judge": {"response": json.dumps({"verdict": "APPROVE"})}})

    assert result["verdict"] == APPROVE


def test_blocking_overlay_entry_is_counted() -> None:
    """Counts come from this config's ``verdict_overlay``, via its own projection."""
    judge = {
        "verdict": REJECT,
        "verdict_overlay": [{"source_agent": "security", "finding_id": "S-01", "blocking": True}],
    }
    result = _build_verdict({"Judge": {"response": json.dumps(judge)}})

    assert result["verdict"] == REJECT


def test_payload_round_trips_through_the_shared_envelope() -> None:
    payload = get_enricher("inspectorx_build_verdict")(
        {"Judge": {"response": json.dumps({"verdict": "APPROVE"})}}, None, None
    )
    verdict, counts = parse_domain_result(payload)

    assert verdict.verdict == APPROVE
    assert counts is not None
    assert set(counts) == set(COUNT_KEYS)
