"""Unit tests for the engine-generic ``claims_coverage`` OVG gate.

The gate enforces that an adjudicator (e.g. the buddies Judge) covers every upstream
reviewer finding: the universe of ``agent::finding_id``s is supplied out-of-band as
``context['upstream_finding_ids']`` and each claim declares what it adjudicates via a
cited-ids field (default ``source_finding_ids``, resolved from ``requires``). The gate is
field-name-agnostic and a no-op when no universe is supplied.
"""

from roundtable.configs.buddies.plugins.gates import claims_coverage_gate
from roundtable.validation.gate_kit import GateRequest


def _claim(sids: list[str], claim_id: str = "J-01") -> dict:
    return {"id": claim_id, "source_finding_ids": sids}


def test_no_universe_is_noop() -> None:
    out = {"claims": [_claim([])]}
    assert claims_coverage_gate(GateRequest("Judge", out, {})) == []
    assert claims_coverage_gate(GateRequest("Judge", out, {"upstream_finding_ids": []})) == []


def test_explicit_empty_universe_rejects_fabricated_citation() -> None:
    out = {"claims": [_claim(["ghost::F-99"])]}
    diagnostics = claims_coverage_gate(GateRequest("Judge", out, {"upstream_finding_ids": []}))
    assert [(diagnostic.path, diagnostic.message) for diagnostic in diagnostics] == [
        ("claims", "claim cites unknown reviewer finding 'ghost::F-99'")
    ]


def test_all_covered_passes() -> None:
    universe = ["a::F-01", "b::F-02"]
    out = {"claims": [_claim(["a::F-01"]), _claim(["b::F-02"], "J-02")]}
    assert claims_coverage_gate(GateRequest("Judge", out, {"upstream_finding_ids": universe})) == []


def test_one_claim_may_cover_many() -> None:
    universe = ["a::F-01", "b::F-02"]
    out = {"claims": [_claim(["a::F-01", "b::F-02"])]}
    assert claims_coverage_gate(GateRequest("Judge", out, {"upstream_finding_ids": universe})) == []


def test_uncovered_finding_flagged() -> None:
    universe = ["a::F-01", "b::F-02"]
    out = {"claims": [_claim(["a::F-01"])]}
    diags = claims_coverage_gate(GateRequest("Judge", out, {"upstream_finding_ids": universe}))
    assert len(diags) == 1
    assert "b::F-02" in diags[0].message
    assert diags[0].path == "claims"


def test_multiple_uncovered_sorted_one_diag_each() -> None:
    universe = ["a::F-03", "a::F-01", "b::F-02"]
    out = {"claims": [_claim([])]}
    diags = claims_coverage_gate(GateRequest("Judge", out, {"upstream_finding_ids": universe}))
    assert [d.message for d in diags] == sorted(d.message for d in diags)
    assert len(diags) == 3


def test_field_names_derived_from_requires() -> None:
    universe = ["a::F-01"]
    out = {"verdicts": [{"cites": ["a::F-01"]}]}
    diags = claims_coverage_gate(
        GateRequest("Judge", out, {"upstream_finding_ids": universe}, requires=["verdicts[].cites"])
    )
    assert diags == []


def test_non_list_claims_is_noop() -> None:
    universe = ["a::F-01"]
    assert (
        claims_coverage_gate(
            GateRequest("Judge", {"claims": None}, {"upstream_finding_ids": universe})
        )
        == []
    )


def test_unknown_cited_ids_are_rejected() -> None:
    universe = ["a::F-01"]
    out = {"claims": [_claim(["a::F-01", "ghost::F-99"])]}
    diags = claims_coverage_gate(GateRequest("Judge", out, {"upstream_finding_ids": universe}))
    assert [(d.path, d.message) for d in diags] == [
        ("claims", "claim cites unknown reviewer finding 'ghost::F-99'")
    ]


def test_duplicate_claim_ids_are_rejected_without_a_coverage_universe() -> None:
    out = {"claims": [_claim([]), _claim([])]}
    diags = claims_coverage_gate(GateRequest("Judge", out, {}))
    assert [(d.path, d.message) for d in diags] == [("claims[1].id", "duplicate claim id 'J-01'")]
