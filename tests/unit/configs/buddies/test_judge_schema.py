"""Cross-record requirements in the claim-only Judge schema."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

_SCHEMA = yaml.safe_load(
    Path("roundtable/configs/buddies/schemas/judge.schema.yaml").read_text(encoding="utf-8")
)
_EXAMPLE = _SCHEMA["examples"][0]
_VALIDATOR = Draft202012Validator(_SCHEMA)


def _document() -> dict:
    return copy.deepcopy(_EXAMPLE)


def _messages(document: dict) -> list[str]:
    return [error.message for error in _VALIDATOR.iter_errors(document)]


def test_example_is_valid() -> None:
    assert _messages(_document()) == []


def test_upheld_claim_requires_evidence() -> None:
    document = _document()
    document["claims"][0]["evidence"] = []
    assert _messages(document)


def test_non_upheld_claim_may_have_no_evidence() -> None:
    document = _document()
    document["claims"][0].update(
        disposition="insufficient_evidence",
        severity="medium",
        evidence=[],
    )
    assert _messages(document) == []


@pytest.mark.parametrize("severity", ["unknown", "critical", ""])
def test_severity_outside_the_scale_is_rejected(severity) -> None:
    """`unknown` outranked `high` in the publication fail-open, so it is no longer a value."""
    document = _document()
    document["claims"][0]["severity"] = severity
    assert _messages(document)


def test_claim_requires_a_reviewer_source() -> None:
    document = _document()
    document["claims"][0]["source_finding_ids"] = []
    assert _messages(document)


@pytest.mark.parametrize(
    "field",
    ["remediation", "prTitle", "prSummary"],
)
def test_removed_claim_fields_are_rejected(field) -> None:
    document = _document()
    document["claims"][0][field] = {}
    assert _messages(document)


@pytest.mark.parametrize("field", ["label", "basis", "escalate"])
def test_derived_verdict_fields_are_rejected(field) -> None:
    document = _document()
    document["verdict"][field] = "value"
    assert _messages(document)


def test_title_and_reason_are_bounded() -> None:
    document = _document()
    document["claims"][0]["title"] = "x" * 101
    document["claims"][0]["reason"] = "x" * 501
    assert len(_messages(document)) == 2


# ── Grounding is one structured, openable record ─────────────────────────────


def test_verdict_requires_the_change_intent() -> None:
    document = _document()
    del document["verdict"]["intent"]
    assert _messages(document)


def test_free_text_evidence_is_rejected() -> None:
    """The shape the free-text `evidence_shape` regex used to police advisorily."""
    document = _document()
    document["claims"][0]["evidence"] = ["src/a.py:42 -- q is concatenated into raw SQL"]
    assert _messages(document)


@pytest.mark.parametrize("field", ["file", "observation", "role"])
def test_evidence_requires_location_observation_and_role(field) -> None:
    document = _document()
    del document["claims"][0]["evidence"][0][field]
    assert _messages(document)


def test_evidence_role_is_a_closed_vocabulary() -> None:
    document = _document()
    document["claims"][0]["evidence"][0]["role"] = "anchor"
    assert _messages(document)


def test_evidence_rejects_an_unmodelled_field() -> None:
    document = _document()
    document["claims"][0]["evidence"][0]["snippet"] = "..."
    assert _messages(document)


def test_counter_evidence_is_optional_and_structured() -> None:
    document = _document()
    document["claims"][0]["counter_evidence"] = [
        {"file": "src/data/SearchService.cs", "start_line": 12, "observation": "callers pre-escape"}
    ]
    assert _messages(document) == []
    document["claims"][0]["counter_evidence"] = ["callers pre-escape"]
    assert _messages(document)


def test_a_thread_cannot_grow_unbounded_grounding() -> None:
    document = _document()
    citation = document["claims"][0]["evidence"][0]
    document["claims"][0]["evidence"] = [copy.deepcopy(citation) for _ in range(5)]
    assert _messages(document)
