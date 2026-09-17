"""Reviewer concern boundaries and the body/schema split they depend on.

Each reviewer owns one concern. These guards pin the wording that keeps the concerns
mutually exclusive, keeps behavioral proof anchored at the execution boundary, and keeps
output-contract mechanics in the schema — which the runtime already appends verbatim —
rather than restated in the prompt body.
"""

from __future__ import annotations

import re

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration


def _flat(text: str) -> str:
    return " ".join(text.split())


def _body(key: str) -> str:
    root = resolve_bundle("buddies")
    entry = get_configuration(root).by_key[key]
    return _flat((root / "prompts" / "Reviewer" / entry.prompt_path).read_text(encoding="utf-8"))


def _schema(name: str) -> str:
    root = resolve_bundle("buddies")
    return _flat((root / "schemas" / f"{name}.schema.yaml").read_text(encoding="utf-8"))


def _require_final_boundary_proof(body: str) -> None:
    assert (
        "reconstruct the final artifact at the boundary where it is parsed, bound, decided" in body
    )
    assert "show the final artifact under the language's normal parsing and binding rules" in body


def _require_same_boundary_counter_case(body: str) -> None:
    assert "check it against the same complete mechanism, at the boundary where the claimed" in body
    assert "one edited fragment is locally consistent does not defeat an interaction" in body


def _require_repository_grounded_remediation(body: str) -> None:
    normalized = body.lower()
    assert re.search(r"restart .{0,80}(?:every|the) proposed", normalized)
    assert "exact locations and observations" in normalized
    assert re.search(
        r"(?:never (?:call|describe) it (?:as )?|must never be described as )verified",
        normalized,
    )
    assert "choose one implementation path" in normalized
    assert "nearest repository-native analogue" in normalized
    assert "decision required to implement" in normalized
    assert "exact blocking prerequisite" in normalized


@pytest.mark.parametrize(
    "agent", ("bigoh", "countercase", "north_star", "redgreen", "smellcheck", "taintcheck")
)
def test_reviewers_challenge_one_repository_grounded_remediation(agent: str) -> None:
    _require_repository_grounded_remediation(_body(agent))


def test_counter_case_proves_behavior_at_the_final_execution_boundary() -> None:
    _require_final_boundary_proof(_body("countercase"))


def test_counter_case_defeats_the_counter_case_at_the_same_boundary() -> None:
    body = _body("countercase")

    _require_same_boundary_counter_case(body)
    critical_rules = body[: body.index("## How you work")]
    assert "at the boundary where the claimed divergence occurs" in critical_rules


def test_counter_case_freezes_proven_claims_before_authoring_remediation() -> None:
    body = _body("countercase")

    assert "freeze the surviving root causes before considering remediation" in body
    assert "A proven claim cannot disappear because its correction is difficult to express" in body


def _require_composition_before_abstaining(body: str) -> None:
    assert "can together establish what neither established alone" in body
    assert "merge them into one candidate and send it back through" in body


def test_counter_case_composes_dropped_candidates_before_abstaining() -> None:
    body = _body("countercase")

    _require_composition_before_abstaining(body)
    critical_rules = body[: body.index("## How you work")]
    assert "A dropped candidate is not a disproved one" in critical_rules
    assert "Compose before you abstain" in body[body.index("Before you emit, confirm") :]


def test_a_composed_candidate_earns_no_shortcut_through_the_gate() -> None:
    body = _body("countercase")

    assert "earns a finding only by passing all six legs, exactly like any other" in body
    assert "abstain on the composed candidate rather than separately on its halves" in body


def test_north_star_hands_every_peer_concern_to_its_owner() -> None:
    body = _body("north_star")

    assert "You do **not** hunt or publish concrete runtime failures" in body
    assert "belong to Red-Green, Big-O, Taint Check and Smell Check" in body


def test_duplication_ownership_is_split_between_north_star_and_smell_check() -> None:
    north_star, smellcheck = _body("north_star"), _body("smellcheck")

    assert "two independently-mutable owners of one decision or rule is yours" in north_star
    assert "repeated code shape that should be extracted is Smell Check's" in north_star
    assert "repeated code shape that should be extracted is yours" in smellcheck
    assert "two independently-mutable owners of one decision or rule is North Star's" in smellcheck
    assert "structural placement and ownership" in smellcheck


def test_north_star_high_tier_scenario_stays_a_structural_proof() -> None:
    assert "failing scenario that proves the structural consequence" in _body("north_star")
    assert "never itself published as a runtime-correctness finding" in _body("north_star")


def test_north_star_states_remediation_before_the_restated_tail() -> None:
    body = _body("north_star")

    assert body.index("Make the target structure concrete") < body.index(
        "Non-negotiables (restated)"
    )


def test_redgreen_keeps_a_failure_mechanism_as_a_hypothesis() -> None:
    body = _body("redgreen")

    assert "A failure mechanism is a hypothesis, not a defect claim" in body
    assert "Do not conclude that production code actually has that defect" in body


@pytest.mark.parametrize(
    ("agent", "schema", "body_must_omit", "schema_must_keep"),
    [
        (
            "redgreen",
            "redgreen",
            ("attempt-wide", "engine rejects an unbacked"),
            "Telemetry is attempt-wide, not finding-specific",
        ),
        (
            "countercase",
            "countercase",
            ("evidence: measured",),
            "so an unbacked `measured` is rejected",
        ),
    ],
)
def test_output_contract_mechanics_live_only_in_the_schema(
    agent: str, schema: str, body_must_omit: tuple[str, ...], schema_must_keep: str
) -> None:
    body = _body(agent)

    assert not [phrase for phrase in body_must_omit if phrase in body]
    assert schema_must_keep in _schema(schema)


def test_boundary_guards_fail_under_mutation() -> None:
    countercase = _body("countercase")

    weakened_trace = countercase.replace("reconstruct the final artifact", "inspect the change")
    with pytest.raises(AssertionError):
        _require_final_boundary_proof(weakened_trace)

    weakened_counter_case = countercase.replace(
        "check it against the same complete mechanism", "go check it"
    )
    with pytest.raises(AssertionError):
        _require_same_boundary_counter_case(weakened_counter_case)

    weakened_composition = countercase.replace(
        "can together establish what neither established alone", "are worth a second look"
    )
    with pytest.raises(AssertionError):
        _require_composition_before_abstaining(weakened_composition)

    weakened_remediation = countercase.replace(
        "must never be described as verified", "should be described as verified"
    ).replace("never call it verified", "call it verified")
    with pytest.raises(AssertionError):
        _require_repository_grounded_remediation(weakened_remediation)


def test_historical_evaluation_case_does_not_leak_into_prompts_or_schemas() -> None:
    bundle = resolve_bundle("buddies")
    reachable = [*(bundle / "prompts").rglob("*.md"), *(bundle / "schemas").rglob("*.yaml")]
    corpus = "\n".join(path.read_text(encoding="utf-8").lower() for path in reachable)
    forbidden = (
        "column fan-out ambiguity",
        "dynamic-sql hygiene",
        "rbacgroupid",
        "getevidenceassociatedalerts",
        "123",
        "456",
    )

    assert not [term for term in forbidden if term in corpus]
