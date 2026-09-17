"""The Buddies verdict derivation — the bundle-owned half of the domain result.

The regression these pin: a schema-VALID Buddies Judge (``verdict`` object +
``claims[]``) used to be read through InspectorX's shape (``verdict`` string +
``verdict_overlay[]``) in shared code, yielding ``UNKNOWN`` with zero counts —
indistinguishable from a Judge that never ran. ``test_schema_example_*`` runs the
config's own ``judge.schema.yaml`` ``examples[0]`` through the real path, so the
bundle's published contract is the fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import roundtable.configs.buddies as buddies
from roundtable.configs.buddies.plugins.verdict import (
    NO_ADJUDICATOR_REASON,
    build_verdict_payload,
    derive_counts,
    derive_label,
    derive_verdict,
    effect_of,
    unknown_verdict,
)
from roundtable.extraction.domain_result import COUNT_KEYS

_SCHEMA = Path(buddies.__file__).parent / "schemas" / "judge.schema.yaml"


# The adjudication cell that derives each release effect. A test names the effect it is
# exercising; the backend no longer takes one, so the fixture supplies the pair it comes
# from and the assertion still runs through the real derivation.
_CELL_FOR_EFFECT = {
    "blocker": ("upheld", "high"),
    "suggestion": ("upheld", "medium"),
    "escalate": ("insufficient_evidence", "medium"),
    "none": ("rejected", "none"),
}


def _claim(**over):
    base = {
        "id": "J-01",
        "source_finding_ids": ["a::F-01"],
        "primary_source_finding_id": "a::F-01",
        "title": "A claim",
        "criterion": "functional_reliability",
        "disposition": "upheld",
        "severity": "medium",
        "reason": "r",
        "evidence": [],
    }
    if "effect" in over:
        base["disposition"], base["severity"] = _CELL_FOR_EFFECT[over.pop("effect")]
    return {**base, **over}


def _payload(_label, claims, *, summary="s", basis=None):
    return {"verdict": {"summary": summary}, "claims": claims}


# ── the published contract is the fixture ────────────────────────────────────
def test_schema_example_is_not_read_as_unknown() -> None:
    example = yaml.safe_load(_SCHEMA.read_text(encoding="utf-8"))["examples"][0]
    verdict, counts = derive_verdict(example)
    assert verdict.verdict == "REJECT"
    assert counts["blocking"] == 2  # the upheld blocker plus the escalated claim
    assert counts["security"] == 1  # of the live claims, one is security_privacy


def test_schema_example_label_needs_no_override() -> None:
    example = yaml.safe_load(_SCHEMA.read_text(encoding="utf-8"))["examples"][0]
    verdict, _ = derive_verdict(example)
    assert verdict.verdict_overridden is False
    assert verdict.reason == example["verdict"]["summary"]


# ── label derivation: first match wins ───────────────────────────────────────
@pytest.mark.parametrize(
    ("disposition", "severity", "effect"),
    [
        ("upheld", "high", "blocker"),
        ("upheld", "medium", "suggestion"),
        ("upheld", "low", "suggestion"),
        ("insufficient_evidence", "medium", "escalate"),
        ("insufficient_evidence", "low", "suggestion"),
        ("rejected", "none", "none"),
        ("not_applicable", "none", "none"),
    ],
)
def test_effect_is_derived_from_the_adjudication_cell(disposition, severity, effect) -> None:
    """Every legal cell derives exactly one effect, so the Judge is never asked for it."""
    assert effect_of({"disposition": disposition, "severity": severity}) == effect


@pytest.mark.parametrize(
    ("disposition", "severity"),
    [
        ("insufficient_evidence", "high"),  # an unproven consequence cannot outrank a blocker
        ("upheld", "none"),
        ("rejected", "low"),
        ("mystery", "high"),
        ("upheld", "mystery"),
    ],
)
def test_an_illegal_cell_derives_no_effect(disposition, severity) -> None:
    """``""`` and not ``none``: ``none`` means "adjudicated away", which would hide this."""
    assert effect_of({"disposition": disposition, "severity": severity}) == ""


@pytest.mark.parametrize(
    ("effects", "label", "basis"),
    [
        ([], "REJECT", "unresolved_blocker"),
        (["blocker"], "REJECT", "proven_failure"),
        (["suggestion", "blocker"], "REJECT", "proven_failure"),
        (["escalate", "suggestion"], "REJECT", "unresolved_blocker"),
        (["blocker", "escalate"], "REJECT", "proven_failure"),
        (["suggestion"], "APPROVE_WITH_SUGGESTIONS", "nonblocking_findings"),
        (["none", "suggestion"], "APPROVE_WITH_SUGGESTIONS", "nonblocking_findings"),
        (["none"], "APPROVE", "clean"),
        (["none", "none"], "APPROVE", "clean"),
    ],
)
def test_derive_label(effects, label, basis) -> None:
    assert derive_label([_claim(effect=e) for e in effects]) == (label, basis)


# ── counts ───────────────────────────────────────────────────────────────────
def test_escalate_counts_as_blocking() -> None:
    """A REJECT must never be reported with zero blocking findings.

    The schema pairs ``escalate`` with basis ``unresolved_blocker`` and a REJECT, so
    excluding it from ``blocking`` would print a verdict its own counts contradict.
    """
    counts = derive_counts([_claim(effect="escalate")])
    assert counts["blocking"] == 1
    assert counts["all"] == 1


def test_adjudicated_away_claims_are_not_counted() -> None:
    counts = derive_counts([_claim(effect="none", disposition="rejected", severity="none")])
    assert counts == dict.fromkeys(COUNT_KEYS, 0)


def test_counts_bucket_by_effect_and_criterion() -> None:
    counts = derive_counts(
        [
            _claim(effect="blocker", criterion="security_privacy"),
            _claim(effect="suggestion", criterion="security_privacy"),
            _claim(effect="suggestion", criterion="maintainability_quality"),
            _claim(effect="none"),
        ]
    )
    assert counts == {"blocking": 1, "nonBlocking": 2, "all": 3, "security": 2}


def test_all_is_the_sum_of_the_two_buckets() -> None:
    counts = derive_counts(
        [_claim(effect=e) for e in ("blocker", "escalate", "suggestion", "none")]
    )
    assert counts["all"] == counts["blocking"] + counts["nonBlocking"]


def test_label_is_derived_and_summary_is_preserved() -> None:
    verdict, _ = derive_verdict(_payload("APPROVE", [_claim(effect="none")], summary="all clear"))
    assert (verdict.verdict, verdict.verdict_overridden) == ("APPROVE", False)
    assert verdict.reason == "all clear"


def test_no_claims_rejects_and_says_why() -> None:
    verdict, counts = derive_verdict(_payload("REJECT", [], basis="unresolved_blocker"))
    assert verdict.verdict == "REJECT"
    assert "no claims" in verdict.reason.lower()
    assert "s" in verdict.reason  # the Judge's own summary is kept, not replaced
    assert counts == dict.fromkeys(COUNT_KEYS, 0)


# ── malformed input degrades, never raises ───────────────────────────────────
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"claims": None},
        {"claims": "not-a-list"},
        {"claims": ["not-a-mapping"]},
        {"verdict": "APPROVE", "claims": []},  # InspectorX's shape
    ],
)
def test_malformed_payloads_do_not_raise(payload) -> None:
    verdict, counts = derive_verdict(payload)
    assert verdict.verdict in {"APPROVE", "APPROVE_WITH_SUGGESTIONS", "REJECT"}
    assert set(counts) == set(COUNT_KEYS)


def test_unknown_verdict_zeroes_every_declared_count() -> None:
    verdict, counts = unknown_verdict("Judge never ran")
    assert verdict.verdict == "UNKNOWN"
    assert counts == dict.fromkeys(COUNT_KEYS, 0)


# ── the enricher: end-to-end through the registered code_fn ──────────────────
def _entry(dep_keys=("Judge",)):
    return SimpleNamespace(dep_keys=dep_keys)


def _outcome(response, valid=True, **over):
    return SimpleNamespace(response=response, valid=valid, **over)


@pytest.fixture(autouse=True)
def _no_reviewer_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adjudicate against an empty reviewer roster unless a test declares one.

    The ambient configuration in a bare pytest process is InspectorX, so the real
    roster would be 19 agents absent from every synthetic snapshot here — coverage
    noise in tests about verdict derivation. An empty roster is the honest neutral:
    a graph that declares no reviewers has none missing.
    """
    from roundtable.configs.buddies.plugins import coverage as coverage_mod

    monkeypatch.setattr(coverage_mod, "_reviewer_keys", lambda: ())


def _with_roster(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    from roundtable.configs.buddies.plugins import coverage as coverage_mod

    monkeypatch.setattr(coverage_mod, "_reviewer_keys", lambda: keys)
    monkeypatch.setattr(coverage_mod, "get_agent_display_name", lambda key, *_: key.upper())


def test_a_partial_roster_annotates_but_keeps_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One dead reviewer must not let a flaky agent veto every review."""
    _with_roster(monkeypatch, "redgreen", "smellcheck")
    payload = json.dumps(_payload("APPROVE", [_claim(effect="none")]))
    result = json.loads(
        build_verdict_payload(
            {
                "Judge": _outcome(payload),
                "smellcheck": _outcome("{}"),
                "redgreen": _outcome("", valid=False, attempts=1, last_error="timeout"),
            },
            _entry(),
        )
    )
    assert result["verdict"] == "APPROVE"
    assert "1 of 2 reviewers reported" in result["reason"]
    assert "REDGREEN" in result["reason"]


def test_a_total_blackout_blocks_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every reviewer died: any label would describe a review that never happened."""
    _with_roster(monkeypatch, "redgreen", "smellcheck")
    payload = json.dumps(_payload("APPROVE", [_claim(effect="none")]))
    result = json.loads(
        build_verdict_payload(
            {
                "Judge": _outcome(payload),
                "smellcheck": _outcome("", valid=False),
                "redgreen": _outcome("", valid=False),
            },
            _entry(),
        )
    )
    assert result["verdict"] == "UNKNOWN"
    assert result["counts"] == dict.fromkeys(COUNT_KEYS, 0)
    assert "0 of 2 reviewers reported" in result["reason"]


def test_a_complete_roster_adds_nothing_to_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_roster(monkeypatch, "redgreen")
    payload = json.dumps(_payload("APPROVE", [_claim(effect="none")], summary="All good."))
    result = json.loads(
        build_verdict_payload({"Judge": _outcome(payload), "redgreen": _outcome("{}")}, _entry())
    )
    assert result["verdict"] == "APPROVE"
    assert "reviewers reported" not in result["reason"]


def test_sink_emits_the_envelope_from_the_judge_dep() -> None:
    payload = json.dumps(_payload("REJECT", [_claim(effect="blocker")], basis="proven_failure"))
    result = json.loads(build_verdict_payload({"Judge": _outcome(payload)}, _entry()))
    assert result["verdict"] == "REJECT"
    assert result["counts"]["blocking"] == 1


def test_sink_follows_dep_keys_not_a_hardcoded_name() -> None:
    payload = json.dumps(_payload("APPROVE", [_claim(effect="none")]))
    result = json.loads(build_verdict_payload({"Arbiter": _outcome(payload)}, _entry(("Arbiter",))))
    assert result["verdict"] == "APPROVE"


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"Judge": _outcome("", valid=False)},
        {"Judge": _outcome("not json at all {")},
    ],
)
def test_sink_degrades_to_unknown_without_raising(snapshot) -> None:
    result = json.loads(build_verdict_payload(snapshot, _entry()))
    assert result["verdict"] == "UNKNOWN"
    assert result["counts"] == dict.fromkeys(COUNT_KEYS, 0)
    assert result["reason"]


def _reason(snapshot, entry=None) -> str:
    return json.loads(build_verdict_payload(snapshot, entry or _entry()))["reason"]


def test_degraded_reasons_distinguish_absent_from_failed_validation() -> None:
    """Same UNKNOWN, different story — and the operator needs the difference.

    A Judge that never ran leaves the run incomplete; one that burned its whole
    retry budget failing validation leaves it unusable and cost real money.
    """
    never_ran = _reason({})
    failed = _reason({"Judge": _outcome("", valid=False, gate="json_schema", attempts=3)})
    assert never_ran != failed
    assert "did not run" in never_ran
    assert "json_schema" in failed and "3 attempts" in failed


def test_degraded_reason_names_the_dep_that_failed() -> None:
    assert "Arbiter" in _reason({}, _entry(("Arbiter",)))


def test_unparseable_output_is_distinct_from_no_output() -> None:
    assert "could not be read" in _reason({"Judge": _outcome("not json at all {")})


def test_a_sink_wired_to_no_adjudicator_says_so() -> None:
    assert _reason({}, _entry(())) == NO_ADJUDICATOR_REASON


def test_replay_shape_without_a_valid_flag_still_adjudicates() -> None:
    """``raw_results.json`` carries only ``response``; replay must not degrade."""
    payload = json.dumps(_payload("APPROVE", [_claim(effect="none")]))
    result = json.loads(build_verdict_payload({"Judge": {"response": payload}}, _entry()))
    assert result["verdict"] == "APPROVE"


def test_sink_never_reaches_for_the_publish_projector() -> None:
    """Publishing is optional; the always-run sink must not depend on it.

    Buddies now declares ``projector: null``, so calling ``projected_counts`` here
    would raise on every single run — and reading InspectorX's ``compute_verdict``
    is the shape mismatch that produced UNKNOWN in the first place.
    """
    import ast
    import inspect

    from roundtable.configs.buddies.plugins import verdict as verdict_mod

    tree = ast.parse(inspect.getsource(verdict_mod))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {
        alias.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for alias in n.names
    }
    assert "projected_counts" not in names
    assert "compute_verdict" not in names
    assert "verdict_overlay" not in names
