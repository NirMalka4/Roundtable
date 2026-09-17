"""A rejection must cost what an agreement costs.

`evidence` used to state a floor for `upheld` alone, `counter_evidence` was optional with a
self-assessed trigger, and neither is published for a rejected claim. Rejecting was therefore
the cheapest disposition in the contract while being the one that silently deletes a peer's
grounded work.

The regression this pins is real. On the evaluated run, J-08 rejected `bigoh::BH-01` citing one
observation labelled `contradiction` — "whitespace input returns a newly constructed empty
dictionary that Serialize then inspects" — which *confirms* bigoh's mechanism rather than
contradicting it. The rejection's actual basis, that the cost is immaterial next to the database
round trip in the same loop, was cited nowhere, and no counter-evidence was recorded. The verdict
was correct; nothing in the artifact showed why.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

import roundtable.configs.buddies as buddies
from roundtable.validation.gates import compile_validator

SCHEMAS = Path(buddies.__file__).parent / "schemas"
JUDGE = yaml.safe_load((SCHEMAS / "judge.schema.yaml").read_text("utf-8"))
VALIDATOR = compile_validator(JUDGE, SCHEMAS)

_GROUNDED_REJECTION: dict[str, Any] = {
    "id": "J-02",
    "source_finding_ids": ["bigoh::BH-01"],
    "primary_source_finding_id": "bigoh::BH-01",
    "title": "Per-order allocation in the search loop",
    "criterion": "performance_efficiency",
    "disposition": "rejected",
    "severity": "none",
    "reason": "The allocation cannot be felt: the same iteration already issues a database round trip.",
    "evidence": [
        {
            "file": "src/data/SearchService.cs",
            "start_line": 55,
            "observation": "each iteration awaits ExecuteReaderAsync before building its row",
            "role": "contradiction",
        }
    ],
    "counter_evidence": [
        {
            "file": "src/data/SearchService.cs",
            "start_line": 57,
            "observation": "a new List is constructed per order, so allocations grow with result size",
        }
    ],
}


def _document(claim: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(JUDGE["examples"][0])
    doc["claims"] = [claim]
    return doc


def _errors(claim: dict[str, Any]) -> list[str]:
    return [e.message for e in VALIDATOR.iter_errors(_document(claim))]


def _without(*keys: str) -> dict[str, Any]:
    return {k: v for k, v in copy.deepcopy(_GROUNDED_REJECTION).items() if k not in keys}


def test_a_fully_grounded_rejection_is_accepted() -> None:
    """Positive control: the floor must be satisfiable, or every rejection below is vacuous."""
    assert not _errors(_GROUNDED_REJECTION)


def test_a_rejection_without_counter_evidence_is_rejected() -> None:
    assert _errors(_without("counter_evidence"))


def test_a_rejection_with_empty_counter_evidence_is_rejected() -> None:
    claim = copy.deepcopy(_GROUNDED_REJECTION)
    claim["counter_evidence"] = []
    assert _errors(claim)


def test_a_rejection_with_no_contradiction_role_is_rejected() -> None:
    """J-08's shape, minus the mislabel: grounding that never argues against the finding."""
    claim = copy.deepcopy(_GROUNDED_REJECTION)
    claim["evidence"][0]["role"] = "defect"
    assert _errors(claim)


def test_a_rejection_with_no_evidence_at_all_is_rejected() -> None:
    claim = copy.deepcopy(_GROUNDED_REJECTION)
    claim["evidence"] = []
    assert _errors(claim)


@pytest.mark.parametrize("disposition", ("insufficient_evidence", "not_applicable"))
def test_an_absence_claim_owes_no_citation(disposition) -> None:
    """No citation can ground an absence, so the floor must not reach these."""
    claim = _without("evidence", "counter_evidence")
    claim["disposition"] = disposition
    claim["evidence"] = []
    claim["severity"] = "none" if disposition == "not_applicable" else "low"
    assert not _errors(claim)


def test_an_upheld_claim_is_not_forced_to_record_counter_evidence() -> None:
    """The floor equalizes cost; it must not make every disposition identical."""
    claim = _without("counter_evidence")
    claim["disposition"] = "upheld"
    claim["severity"] = "low"
    claim["evidence"][0]["role"] = "defect"
    assert not _errors(claim)


def test_the_canonical_example_demonstrates_a_grounded_rejection() -> None:
    """The example is the model's only worked instance of a contract path.

    A rung with no demonstration is how north_star came to emit prose for two runs, and how
    J-08 came to label a confirmation as a contradiction.
    """
    rejections = [c for c in JUDGE["examples"][0]["claims"] if c["disposition"] == "rejected"]
    assert rejections, "the example shows no rejection, so the tightened path is undemonstrated"
    for claim in rejections:
        assert any(e["role"] == "contradiction" for e in claim["evidence"])
        assert claim["counter_evidence"]


def test_the_contradiction_role_says_what_does_not_qualify() -> None:
    """The enum text is the only thing standing between `contradiction` and a restatement."""
    role = JUDGE["properties"]["claims"]["items"]["properties"]["evidence"]["items"]["properties"][
        "role"
    ]["description"]
    assert "never a `contradiction`" in role
    assert "bounds that consequence" in role


def test_the_judge_prompt_carries_the_rejection_procedure() -> None:
    """A schema rule the prompt never states is a rule the model meets only as a retry."""
    raw = (
        Path(buddies.__file__).parent / "prompts" / "Reviewer" / "Agents" / "judge.agent.md"
    ).read_text("utf-8")
    prompt = " ".join(raw.split())
    assert "### Rejecting a reviewer's finding" in raw
    assert "does not contradict them" in prompt
    assert "record the strongest part of the reviewer's case against yourself" in prompt
