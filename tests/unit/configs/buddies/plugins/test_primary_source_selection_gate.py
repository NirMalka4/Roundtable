"""Cross-field validation for the Judge's primary reviewer source."""

from roundtable.configs.buddies.plugins.gates import primary_source_selection_gate
from roundtable.validation.gate_kit import GateRequest


def _claim(primary: str | None, cited: list[str] | None = None) -> dict:
    return {
        "source_finding_ids": cited or ["a::F-01"],
        "primary_source_finding_id": primary,
    }


def test_primary_source_may_belong_to_the_claim() -> None:
    output = {"claims": [_claim("a::F-01", ["a::F-01", "b::F-02"])]}
    assert primary_source_selection_gate(GateRequest("Judge", output, {})) == []


def test_primary_source_outside_the_claim_is_rejected() -> None:
    output = {"claims": [_claim("b::F-02")]}
    diagnostics = primary_source_selection_gate(GateRequest("Judge", output, {}))
    assert [(diagnostic.path, diagnostic.message) for diagnostic in diagnostics] == [
        (
            "claims[0].primary_source_finding_id",
            "must identify a reviewer finding adjudicated by this claim",
        )
    ]


def test_non_string_selection_is_left_to_json_schema() -> None:
    output = {"claims": [_claim(None)]}
    assert primary_source_selection_gate(GateRequest("Judge", output, {})) == []
