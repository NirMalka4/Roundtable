"""Round-trip tests for the domain-result ENVELOPE (``output/domain_result.py``).

A config's terminal ``kind: code`` node emits ``build_domain_result(...)`` as JSON;
the post-graph presentation layer re-hydrates it with ``parse_domain_result(...)``.
These lock that the two ends agree — the payload shape is the SSOT shared by the
producer (a bundle's ``build_verdict`` enricher) and the reader.

The envelope is deliberately **shape-blind**: it takes an already-computed verdict +
counts and never reads a run snapshot, so it cannot silently favour one config's
Judge over another's. The "degraded Judge ⇒ UNKNOWN + zero counts, never raise"
contract therefore belongs to whichever bundle derives it — guarded in
``tests/unit/configs/inspectorx/plugins/test_verdict_enricher.py``.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap

import pytest

from roundtable.decision import APPROVE
from roundtable.decision.verdict import VerdictResult
from roundtable.extraction.domain_result import (
    COUNT_KEYS,
    build_domain_result,
    parse_domain_result,
)


def _verdict(label: str = APPROVE) -> VerdictResult:
    return VerdictResult(
        verdict=label,
        verdict_icon="\u2705",
        verdict_overridden=False,
        reason="Review verdict: APPROVE. 0 blocking, 0 non-blocking findings.",
    )


def test_build_then_parse_round_trips_verdict_and_counts() -> None:
    counts = {"blocking": 2, "nonBlocking": 3, "all": 5, "security": 1}
    payload = json.dumps(build_domain_result(_verdict(), counts))

    verdict, parsed_counts = parse_domain_result(payload)

    assert verdict.verdict == APPROVE
    assert parsed_counts == counts


def test_build_payload_carries_the_declared_keys() -> None:
    result = build_domain_result(_verdict(), {})
    assert set(result) == {"verdict", "verdictIcon", "verdictOverridden", "reason", "counts"}


def test_build_projects_counts_onto_the_declared_vocabulary() -> None:
    """A config's own count vocabulary is projected: unknown keys drop, absent ⇒ 0.

    Each config owns its counting; the envelope persists exactly ``COUNT_KEYS`` so
    trace.json stays stable for the report and the acceptance grader.
    """
    result = build_domain_result(_verdict(), {"blocking": 4, "claimsUpheld": 9})

    assert set(result["counts"]) == set(COUNT_KEYS)
    assert result["counts"]["blocking"] == 4
    assert result["counts"]["security"] == 0
    assert "claimsUpheld" not in result["counts"]


def test_build_never_reads_a_run_snapshot() -> None:
    """The envelope is shape-blind — it takes values, so no Judge shape can leak in.

    Pinned because assembly used to read the Judge here, which silently produced
    UNKNOWN + zero counts for every config but the one it was written for. Checked
    against the executable body only: the docstring is free to explain the history.
    """
    assert list(inspect.signature(build_domain_result).parameters) == ["verdict", "counts"]

    tree = ast.parse(textwrap.dedent(inspect.getsource(build_domain_result)))
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        fn.body = fn.body[1:]  # drop the docstring — prose is not behaviour
    body = ast.unparse(fn)

    for shape_token in ("Judge", "verdict_overlay", "claims", "session_results", "projector"):
        assert shape_token not in body


def test_parse_rejects_malformed_json() -> None:
    with pytest.raises(ValueError):
        parse_domain_result("not json at all {")


def test_parse_rejects_payload_without_verdict() -> None:
    with pytest.raises(KeyError):
        parse_domain_result(json.dumps({"counts": {}}))


def test_parse_tolerates_absent_counts() -> None:
    """A payload with no ``counts`` block parses to ``counts is None`` (fallback path)."""
    verdict, counts = parse_domain_result(json.dumps({"verdict": "APPROVE"}))
    assert verdict.verdict == APPROVE
    assert counts is None
