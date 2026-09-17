"""The neutral publish contract (``output.publishable.PublishableResult``).

Pins the topology-agnostic result Increment B introduced: the slim value a
``Projector`` produces and the generic publish/unpublish flow consumes. It carries
only generic types (findings list, session id, verdict string, a count map, and two
optional topology signals) — no Roundtable-shaped attribute — so ``run_publish`` /
``run_unpublish`` never read a ``PublishPlan`` field.
"""

from __future__ import annotations

import dataclasses

import pytest

from roundtable.delivery.publishable import PublishableResult


def test_defaults_are_neutral() -> None:
    result = PublishableResult(all_findings=[], session_id="s1", verdict="APPROVE")
    assert result.counts == {}
    assert result.abort_reason is None
    assert result.log_summary is None


def test_is_frozen() -> None:
    result = PublishableResult(all_findings=[], session_id="s1", verdict="APPROVE")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.verdict = "REJECT"  # type: ignore[misc]


def test_carries_topology_signals() -> None:
    result = PublishableResult(
        all_findings=[],
        session_id="s1",
        verdict="REJECT",
        counts={"all": 3},
        abort_reason="count parity violation",
        log_summary="plan: verdict=REJECT",
    )
    assert result.counts == {"all": 3}
    assert result.abort_reason == "count parity violation"
    assert result.log_summary == "plan: verdict=REJECT"
