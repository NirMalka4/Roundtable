"""Verdict invariant gate.

These two named tests are the ENTIRE safety net for the two verdict invariants
whose teeth are not exercised by a normal review (a parseable
Judge with a matching verdict never *flips* the auto-override). A regression that
broke the override-flip or the empty-plan path would otherwise pass silently —
these synthetic-input tests are what actually catch it.

Do not delete or weaken these tests without re-homing their coverage.
"""

from __future__ import annotations

import json

from roundtable.bundle import resolve_bundle
from roundtable.configs.inspectorx.plugins.verdict import (
    APPROVE,
    APPROVE_WITH_SUGGESTIONS,
    REJECT,
    UNKNOWN,
)
from roundtable.configs.inspectorx.plugins.verdict import compute_verdict as _compute_verdict
from roundtable.graph import get_configuration

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def compute_verdict(session_results, session_dir_path=None):
    return _compute_verdict(session_results, CONFIGURATION, session_dir_path)


# ── Invariant (a): no valid Judge ⇒ cannot APPROVE ──────────────────────────


def test_verdict_requires_judge_missing_agent() -> None:
    """No Judge agent in the session ⇒ UNKNOWN (never APPROVE)."""
    result = compute_verdict({"Analyst": {"response": '{"verdict":"APPROVE"}'}})
    assert result.verdict == UNKNOWN
    assert result.verdict not in (APPROVE, APPROVE_WITH_SUGGESTIONS)


def test_verdict_requires_judge_empty_response() -> None:
    """Judge present but with an empty response ⇒ UNKNOWN."""
    result = compute_verdict({"Judge": {"response": ""}})
    assert result.verdict == UNKNOWN
    assert result.verdict not in (APPROVE, APPROVE_WITH_SUGGESTIONS)


def test_verdict_requires_judge_unparseable_response() -> None:
    """Judge present but unparseable ⇒ UNKNOWN (cannot fabricate an APPROVE)."""
    result = compute_verdict({"Judge": {"response": "this is not json at all {"}})
    assert result.verdict == UNKNOWN
    assert result.verdict not in (APPROVE, APPROVE_WITH_SUGGESTIONS)


def test_verdict_requires_judge_even_when_other_agents_approve() -> None:
    """A non-Judge agent claiming APPROVE must NOT leak into the verdict."""
    result = compute_verdict(
        {
            "Analyst": {"response": '{"verdict":"APPROVE"}'},
            "Judge": {"response": ""},
        }
    )
    assert result.verdict == UNKNOWN


def test_unknown_verdicts_say_which_failure_they_hit() -> None:
    """All UNKNOWN, but "never ran", "said nothing", and "failed validation" differ.

    The third burned the agent's whole retry budget and means the review is
    unusable rather than merely incomplete — the reason line has to carry that.
    """
    from types import SimpleNamespace

    missing = compute_verdict({})
    empty = compute_verdict({"Judge": {"response": ""}})
    rejected = compute_verdict(
        {
            "Judge": SimpleNamespace(
                response="", valid=False, gate="json_schema", attempts=3, last_error="no verdict"
            )
        }
    )
    assert len({missing.reason, empty.reason, rejected.reason}) == 3
    assert "did not run" in missing.reason
    assert "json_schema" in rejected.reason and "3 attempts" in rejected.reason


def test_a_readable_but_unparseable_judge_is_not_reported_as_a_validation_failure() -> None:
    """The Judge answered; the answer was malformed. That is not an OVG reject."""
    parse_failure = compute_verdict({"Judge": {"response": "this is not json at all {"}})
    assert "did not run" not in parse_failure.reason
    assert "output validation" not in parse_failure.reason
    assert "gate" not in parse_failure.reason


# ── Invariant (b): any Critical finding ⇒ verdict forced to REJECT ───────────


def _judge_with(verdict: str, critical: bool) -> str:
    overlay = [
        {
            "source_agent": "Analyst",
            "finding_id": "F1",
            "blocking": critical,
            "verdict_severity": "critical" if critical else "low",
        }
    ]
    return json.dumps({"verdict": verdict, "verdict_overlay": overlay})


def test_critical_finding_forces_reject_from_approve() -> None:
    """Judge says APPROVE but a Critical overlay severity ⇒ REJECT (override flips)."""
    result = compute_verdict({"Judge": {"response": _judge_with("APPROVE", critical=True)}})
    assert result.verdict == REJECT
    assert result.verdict_overridden is True


def test_critical_finding_forces_reject_from_approve_with_suggestions() -> None:
    """Judge says APPROVE_WITH_SUGGESTIONS but Critical present ⇒ REJECT."""
    result = compute_verdict(
        {"Judge": {"response": _judge_with("APPROVE_WITH_SUGGESTIONS", critical=True)}}
    )
    assert result.verdict == REJECT
    assert result.verdict_overridden is True


def test_no_critical_does_not_over_fire_override() -> None:
    """Control: no Critical ⇒ Judge's stated APPROVE is trusted (override silent)."""
    result = compute_verdict({"Judge": {"response": _judge_with("APPROVE", critical=False)}})
    assert result.verdict == APPROVE
    assert result.verdict_overridden is False


def test_critical_with_judge_already_reject_does_not_mark_overridden() -> None:
    """Critical + Judge already REJECT ⇒ REJECT but NOT marked overridden (no flip)."""
    result = compute_verdict({"Judge": {"response": _judge_with("REJECT", critical=True)}})
    assert result.verdict == REJECT
    assert result.verdict_overridden is False
