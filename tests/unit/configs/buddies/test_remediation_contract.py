"""Buddies reviewers share one non-applyable remediation draft contract."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

import roundtable.configs.buddies as buddies
from roundtable.validation.gates import compile_validator

SCHEMAS = Path(buddies.__file__).parent / "schemas"
CONTRACT = yaml.safe_load((SCHEMAS / "_shared" / "remediation.schema.yaml").read_text("utf-8"))
REVIEWERS = ("bigoh", "countercase", "smellcheck", "taintcheck", "north_star", "redgreen")
REQUIRED = ["rationale", "proposal", "illustration", "checks", "limitations"]
FIELDS = {*REQUIRED, "language"}
REMOVED = {
    "kind",
    "anchorIndex",
    "replacement",
    "code",
    "prose",
    "fallbackReason",
}


def _schema(name: str) -> dict[str, Any]:
    return yaml.safe_load((SCHEMAS / f"{name}.schema.yaml").read_text("utf-8"))


def _remediation(name: str) -> dict[str, Any]:
    return _schema(name)["properties"]["findings"]["items"]["properties"]["remediation"]


def _validator(name: str):
    return compile_validator(_schema(name), SCHEMAS)


def _document(name: str) -> dict[str, Any]:
    document = copy.deepcopy(_schema(name)["examples"][0])
    document["findings"] = [copy.deepcopy(document["findings"][0])]
    return document


@pytest.mark.parametrize("name", REVIEWERS)
def test_every_reviewer_inlines_the_single_shared_shape(name: str) -> None:
    remediation = _remediation(name)
    shared = CONTRACT["$defs"].get("remediation")
    assert shared is not None
    assert remediation["required"] == shared["required"] == REQUIRED
    assert remediation["additionalProperties"] is False
    assert set(remediation["properties"]) == FIELDS
    assert not REMOVED & remediation["properties"].keys()


@pytest.mark.parametrize("name", REVIEWERS)
def test_every_reviewer_example_is_a_complete_non_applyable_draft(name: str) -> None:
    remediation = _document(name)["findings"][0]["remediation"]
    assert all(remediation[field] for field in ("rationale", "proposal", "illustration", "checks"))
    assert isinstance(remediation["limitations"], list)
    assert not REMOVED & remediation.keys()
    assert list(_validator(name).iter_errors(_document(name))) == []


@pytest.mark.parametrize("name", REVIEWERS)
@pytest.mark.parametrize("field", REQUIRED)
def test_each_required_draft_component_is_enforced(name: str, field: str) -> None:
    document = _document(name)
    document["findings"][0]["remediation"].pop(field)
    errors = list(_validator(name).iter_errors(document))
    assert any(error.validator == "required" for error in errors)


@pytest.mark.parametrize("name", REVIEWERS)
@pytest.mark.parametrize("field", sorted(REMOVED))
def test_legacy_apply_ready_fields_are_rejected(name: str, field: str) -> None:
    document = _document(name)
    document["findings"][0]["remediation"][field] = 0 if field == "anchorIndex" else "x"
    errors = list(_validator(name).iter_errors(document))
    assert any(error.validator == "additionalProperties" for error in errors)


@pytest.mark.parametrize("name", REVIEWERS)
def test_checks_and_limitations_retain_unbounded_investigation_detail(name: str) -> None:
    remediation = _remediation(name)["properties"]
    assert remediation["checks"]["minItems"] == 1
    assert "maxItems" not in remediation["checks"]
    assert "maxItems" not in remediation["limitations"]
    assert remediation["checks"]["items"]["required"] == [
        "filePath",
        "startLine",
        "endLine",
        "observation",
    ]


@pytest.mark.parametrize("name", REVIEWERS)
def test_illustration_language_is_optional_and_cannot_make_it_applyable(name: str) -> None:
    document = _document(name)
    remediation = document["findings"][0]["remediation"]
    remediation.pop("language", None)
    assert list(_validator(name).iter_errors(document)) == []
