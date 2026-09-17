"""Reviewer coverage must be sayable on every surface a human reads.

The regression these pin: on PR 123 the test-adequacy reviewer timed out, the
Judge adjudicated the five survivors, and ``verdict.md`` and the PR comment rendered
exactly what a healthy six-reviewer run renders. "RedGreen found nothing" and "RedGreen
never finished" were indistinguishable, so a reader inferred full coverage from a
review that did not have it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from roundtable.configs.buddies.plugins import coverage as coverage_mod
from roundtable.configs.buddies.plugins.coverage import (
    coverage_markdown,
    reviewer_coverage,
)


@pytest.fixture(autouse=True)
def _roster(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two reviewers, named as the graph would name them."""
    monkeypatch.setattr(coverage_mod, "_reviewer_keys", lambda: ("redgreen", "smellcheck"))
    monkeypatch.setattr(coverage_mod, "get_agent_display_name", lambda key, *_: key.upper())


def _delivered():
    return SimpleNamespace(response="{}", valid=True)


def _timed_out():
    """The shape a real 600 s breach leaves behind: no gate reached, one attempt."""
    return SimpleNamespace(
        response="",
        valid=False,
        gate=None,
        attempts=1,
        last_error="timeout",
        attempts_detail=[SimpleNamespace(outcome="api_error")],
    )


def test_a_full_roster_is_complete() -> None:
    coverage = reviewer_coverage({"redgreen": _delivered(), "smellcheck": _delivered()})
    assert coverage.complete
    assert not coverage.total_blackout
    assert coverage.summary_line() == "All 2 reviewers reported."


def test_a_missing_reviewer_is_named_with_its_root_cause() -> None:
    coverage = reviewer_coverage({"redgreen": _timed_out(), "smellcheck": _delivered()})
    assert not coverage.complete
    assert not coverage.total_blackout
    assert "1 of 2 reviewers reported" in coverage.summary_line()
    assert "REDGREEN" in coverage.summary_line()
    ((_name, reason),) = coverage.degraded
    assert "timed out" in reason


def test_an_agent_absent_from_the_snapshot_counts_as_degraded() -> None:
    """A reviewer the scheduler never launched leaves no key at all."""
    coverage = reviewer_coverage({"smellcheck": _delivered()})
    assert [n for n, _ in coverage.degraded] == ["REDGREEN"]
    assert "did not run" in coverage.degraded[0][1]


def test_every_reviewer_dead_is_a_total_blackout() -> None:
    coverage = reviewer_coverage({})
    assert coverage.total_blackout
    assert "0 of 2 reviewers reported" in coverage.summary_line()


def test_an_empty_roster_is_complete_not_a_blackout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A graph declaring no reviewers has none missing — the guard the sink relies on."""
    monkeypatch.setattr(coverage_mod, "_reviewer_keys", lambda: ())
    coverage = reviewer_coverage({})
    assert coverage.complete
    assert not coverage.total_blackout


def test_complete_coverage_is_still_stated_positively() -> None:
    """Absence of a warning is only trustworthy if its presence was possible."""
    block = coverage_markdown(
        reviewer_coverage({"redgreen": _delivered(), "smellcheck": _delivered()})
    )
    assert any("All 2 reviewers reported." in line for line in block)


def test_degraded_coverage_renders_the_warning_and_each_reason() -> None:
    block = "\n".join(coverage_markdown(reviewer_coverage({"smellcheck": _delivered()})))
    assert "Degraded coverage" in block
    assert "REDGREEN" in block
    assert "did not run" in block


def test_the_roster_is_derived_from_the_graph_not_listed() -> None:
    """A reviewer added to ``agent_graph.yaml`` must be covered without editing code."""
    from pathlib import Path

    source = Path(coverage_mod.__file__).read_text(encoding="utf-8")
    assert "finding_producing_agent_keys" in source
    for hardcoded in ("redgreen", "smellcheck", "taintcheck", "bigoh"):
        assert hardcoded not in source
