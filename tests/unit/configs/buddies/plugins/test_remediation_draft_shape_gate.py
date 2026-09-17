"""remediation_draft_shape gate: one complete, non-applyable draft."""

from __future__ import annotations

from roundtable.configs.buddies.plugins.gates import remediation_draft_shape_gate
from roundtable.validation.gate_kit import GateRequest

_REQ = ["findings[].remediation"]


def _run(remediation: object) -> list[str]:
    request = GateRequest(
        "reviewer",
        {"findings": [{"remediation": remediation}]},
        {},
        requires=_REQ,
    )
    return [diagnostic.path for diagnostic in remediation_draft_shape_gate(request)]


def _remediation() -> dict:
    return {
        "rationale": "The boundary now owns normalization.",
        "proposal": "Normalize at the shared boundary and update both consumers.",
        "illustration": "normalized = normalize(value)",
        "checks": [
            {
                "filePath": "src/a.py",
                "startLine": 4,
                "endLine": 6,
                "observation": "Both consumers receive the boundary result.",
            }
        ],
        "limitations": [],
    }


def test_complete_draft_passes() -> None:
    assert _run(_remediation()) == []


def test_missing_or_blank_narrative_components_fail_at_exact_paths() -> None:
    remediation = _remediation()
    remediation.pop("proposal")
    remediation["illustration"] = " "
    assert _run(remediation) == [
        "findings[0].remediation.proposal",
        "findings[0].remediation.illustration",
    ]


def test_checks_require_an_ordered_positive_range_and_observation() -> None:
    remediation = _remediation()
    remediation["checks"] = [{"filePath": "", "startLine": 3, "endLine": 2, "observation": " "}]
    assert _run(remediation) == [
        "findings[0].remediation.checks[0].filePath",
        "findings[0].remediation.checks[0].endLine",
        "findings[0].remediation.checks[0].observation",
    ]


def test_checks_and_limitations_have_no_count_cap() -> None:
    remediation = _remediation()
    remediation["checks"] *= 8
    remediation["limitations"] = [f"gap {index}" for index in range(5)]
    assert _run(remediation) == []


def test_missing_remediation_fails_and_non_finding_payload_is_ignored() -> None:
    request = GateRequest(
        "reviewer",
        {"findings": [{}]},
        {},
        requires=_REQ,
    )
    assert [d.path for d in remediation_draft_shape_gate(request)] == ["findings[0].remediation"]
    assert (
        remediation_draft_shape_gate(GateRequest("reviewer", {"summary": "x"}, {}, requires=_REQ))
        == []
    )
