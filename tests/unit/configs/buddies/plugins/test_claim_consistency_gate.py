"""Unit tests for the claim-consistency OVG gate."""

from pathlib import Path

import pytest
import yaml

import roundtable.configs.buddies as buddies
from roundtable.configs.buddies.plugins.gates import claim_consistency_gate
from roundtable.validation.gate_kit import GateRequest

_SCHEMA = Path(buddies.__file__).parent / "schemas" / "judge.schema.yaml"


def _out(disposition: str, severity: str) -> dict:
    return {
        "verdict": {"summary": "s"},
        "claims": [
            {
                "id": "J-01",
                "disposition": disposition,
                "severity": severity,
            }
        ],
    }


def _paths(output: dict) -> list[str]:
    return [d.path for d in claim_consistency_gate(GateRequest("Judge", output, {}))]


def test_schema_example_passes() -> None:
    example = yaml.safe_load(_SCHEMA.read_text(encoding="utf-8"))["examples"][0]
    assert claim_consistency_gate(GateRequest("Judge", example, {})) == []


@pytest.mark.parametrize(
    ("disposition", "severity"),
    [
        ("upheld", "high"),
        ("upheld", "medium"),
        ("upheld", "low"),
        ("insufficient_evidence", "medium"),
        ("insufficient_evidence", "low"),
        ("rejected", "none"),
        ("not_applicable", "none"),
    ],
)
def test_consistent_cells_pass(disposition, severity) -> None:
    assert _paths(_out(disposition, severity)) == []


@pytest.mark.parametrize(
    ("disposition", "severity", "path"),
    [
        ("rejected", "low", "claims[0].severity"),
        ("upheld", "none", "claims[0].severity"),
        ("insufficient_evidence", "high", "claims[0].severity"),
        ("insufficient_evidence", "none", "claims[0].severity"),
    ],
)
def test_inconsistent_cells_fail(disposition, severity, path) -> None:
    assert path in _paths(_out(disposition, severity))


def test_insufficient_evidence_can_never_reach_high() -> None:
    """`unknown` outranked `high` at the publication threshold; `high` now needs `upheld`."""
    assert "claims[0].severity" in _paths(_out("insufficient_evidence", "high"))


@pytest.mark.parametrize("output", [{}, {"claims": "bad"}, {"claims": ["bad"]}])
def test_invalid_container_shape_is_left_to_json_schema(output) -> None:
    assert claim_consistency_gate(GateRequest("Judge", output, {})) == []
