"""``unreadable_reason`` must keep four outcomes distinguishable.

``response_of`` collapses "never ran", "ran and failed validation", and "ran and
said nothing" onto one ``None``. A terminal reader that reports all three as
"no output" hides the only one that means the run is *unusable* rather than
merely incomplete, so each state has to produce its own sayable reason.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from roundtable.result_access import (
    RUNTIME_FAILURE_OUTCOME,
    TIMEOUT_ERROR,
    unreadable_reason,
)


def _outcome(**over):
    base = {
        "response": "{}",
        "valid": True,
        "gate": None,
        "attempts": 0,
        "last_error": "",
        "attempts_detail": [],
    }
    return SimpleNamespace(**{**base, **over})


def test_readable_output_is_not_a_reason() -> None:
    assert unreadable_reason(_outcome(), agent="Judge") is None


def test_absent_agent_says_it_never_ran() -> None:
    reason = unreadable_reason(None, agent="Judge")
    assert reason == "Judge did not run."


def test_empty_response_is_distinct_from_never_running() -> None:
    reason = unreadable_reason(_outcome(response=""), agent="Judge")
    assert reason == "Judge produced an empty response."
    assert reason != unreadable_reason(None, agent="Judge")


def test_validation_failure_names_the_gate_the_attempts_and_the_error() -> None:
    reason = unreadable_reason(
        _outcome(
            response="", valid=False, gate="json_schema", attempts=3, last_error="claims: required"
        ),
        agent="Judge",
    )
    assert "json_schema" in reason
    assert "3 attempts" in reason
    assert "claims: required" in reason


def test_validation_failure_is_reported_even_without_diagnostics() -> None:
    reason = unreadable_reason(_outcome(response="", valid=False), agent="Judge")
    assert "failed output validation" in reason
    assert reason != unreadable_reason(_outcome(response=""), agent="Judge")


def _timed_out(**over):
    """An outcome shaped like a real timeout: no gate reached, one terminal attempt."""
    base = {
        "response": "",
        "valid": False,
        "gate": None,
        "attempts": 1,
        "last_error": TIMEOUT_ERROR,
        "attempts_detail": [SimpleNamespace(outcome=RUNTIME_FAILURE_OUTCOME)],
    }
    return _outcome(**{**base, **over})


def test_timeout_is_not_reported_as_a_validation_failure() -> None:
    """RedGreen burned its whole budget and never reached a gate; calling that a
    validation failure sends an operator to the output contract to fix a time budget."""
    reason = unreadable_reason(_timed_out(), agent="RedGreen")
    assert "timed out" in reason
    assert "validation" not in reason
    assert "gate" not in reason


def test_non_timeout_runtime_failure_is_distinct_from_both() -> None:
    reason = unreadable_reason(_timed_out(last_error="exit=1, empty=True"), agent="RedGreen")
    assert "the model run failed" in reason
    assert "exit=1" in reason
    assert reason != unreadable_reason(_timed_out(), agent="RedGreen")


def test_a_gate_rejection_still_names_the_gate() -> None:
    """The runtime-cause branch must not swallow the OVG case it sits in front of."""
    reason = unreadable_reason(
        _outcome(
            response="",
            valid=False,
            gate="json_schema",
            attempts=3,
            attempts_detail=[SimpleNamespace(outcome="ovg_reject")],
        ),
        agent="Judge",
    )
    assert "failed the json_schema gate" in reason
    assert "timed out" not in reason


def test_every_failure_state_reads_differently() -> None:
    reasons = {
        unreadable_reason(None, agent="Judge"),
        unreadable_reason(_outcome(response=""), agent="Judge"),
        unreadable_reason(_outcome(response="", valid=False, gate="json_schema"), agent="Judge"),
    }
    assert len(reasons) == 3


@pytest.mark.parametrize(
    "result",
    [{"response": "{}"}, SimpleNamespace(response="{}")],
)
def test_replay_shapes_without_a_valid_flag_stay_readable(result) -> None:
    """``raw_results.json`` carries only ``response``; a missing flag is not a failure."""
    assert unreadable_reason(result, agent="Judge") is None


def test_mapping_shape_reports_a_recorded_validation_failure() -> None:
    reason = unreadable_reason({"response": "", "valid": False}, agent="Judge")
    assert reason is not None
    assert "Judge" in reason
