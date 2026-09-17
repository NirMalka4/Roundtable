"""The typed abstention record Counter Case owes for a candidate it drops.

An abstention is terminal: the Judge is told to use these records only as scope or
counterevidence context and never to raise one into a claim. So the record is not a
report — it is the price of dropping a candidate. Every kind but `counter_case_held`
states something the reviewer could not establish; `counter_case_held` alone asserts a
positive fact about the code, so it is the one kind that must exhibit the boundary it
claims to have checked.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

_SCHEMA = yaml.safe_load(
    Path("roundtable/configs/buddies/schemas/countercase.schema.yaml").read_text(encoding="utf-8")
)
_EXAMPLE = _SCHEMA["examples"][0]
_VALIDATOR = Draft202012Validator(_SCHEMA)
_KINDS = _SCHEMA["properties"]["abstentions"]["items"]["properties"]["kind"]["enum"]


def _document() -> dict:
    return copy.deepcopy(_EXAMPLE)


def _messages(document: dict) -> list[str]:
    return [error.message for error in _VALIDATOR.iter_errors(document)]


def _abstention(document: dict, kind: str) -> dict:
    return next(record for record in document["abstentions"] if record["kind"] == kind)


def test_example_is_valid() -> None:
    assert _messages(_document()) == []


def test_the_kinds_cover_every_leg_that_can_stop_a_candidate() -> None:
    assert _KINDS == [
        "contract_not_located",
        "trace_incomplete",
        "no_reachable_caller",
        "counter_case_held",
        "pre_existing",
        "budget",
    ]


def test_a_dropped_candidate_names_the_leg_that_stopped_it() -> None:
    document = _document()
    del _abstention(document, "pre_existing")["kind"]

    assert "'kind' is a required property" in _messages(document)


def test_an_unlisted_leg_is_rejected() -> None:
    document = _document()
    _abstention(document, "pre_existing")["kind"] = "not_worth_it"

    assert _messages(document) != []


def test_a_held_counter_case_must_exhibit_the_boundary_it_checked() -> None:
    document = _document()
    del _abstention(document, "counter_case_held")["resolvedArtifact"]

    assert "'resolvedArtifact' is a required property" in _messages(document)


@pytest.mark.parametrize("kind", [kind for kind in _KINDS if kind != "counter_case_held"])
def test_a_leg_the_reviewer_could_not_establish_owes_no_artifact(kind: str) -> None:
    document = _document()
    record = _abstention(document, "pre_existing")
    record["kind"] = kind
    record.pop("resolvedArtifact", None)

    assert _messages(document) == []


def test_the_artifact_description_rejects_a_restated_conclusion() -> None:
    described = _SCHEMA["properties"]["abstentions"]["items"]["properties"]["resolvedArtifact"][
        "description"
    ]

    assert "Show what a reader can check, not the conclusion you drew from it" in described
    assert "local consistency of the edited" in described
