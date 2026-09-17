"""Characterization tests for judge_summary severity-count initialization.

These lock the observable behavior of the two functions that build the
``severity_counts`` dict (``compute_overlay_severity_counts`` and the empty/default
summary) so a refactor of the initialization idiom — e.g. a dict comprehension to
``dict.fromkeys`` (ruff C420) — is provably behavior-preserving: every canonical
bucket is present, defaults to 0, and tallies correctly.
"""

from __future__ import annotations

from roundtable.bundle import resolve_bundle
from roundtable.configs.inspectorx.plugins.verdict import (
    _empty_overlay_summary,
    compute_overlay_severity_counts,
    severity_keys,
)
from roundtable.graph import get_configuration

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def test_overlay_counts_empty_has_all_buckets_zeroed() -> None:
    counts = compute_overlay_severity_counts([], CONFIGURATION)
    assert counts == dict.fromkeys(severity_keys(CONFIGURATION), 0)
    assert list(counts.keys()) == list(severity_keys(CONFIGURATION))


def test_overlay_counts_tally_by_bucket() -> None:
    overlay = [
        {"verdict_severity": "Critical"},
        {"verdict_severity": "high"},
        {"verdict_severity": "blocking"},  # maps to high
        {"verdictSeverity": "medium"},  # camelCase ⇒ ignored (no alias)
        {},  # no severity -> contributes nothing
        {"verdict_severity": 5},  # non-str -> ignored
    ]
    counts = compute_overlay_severity_counts(overlay, CONFIGURATION)
    assert counts == {"critical": 1, "high": 2, "medium": 0, "low": 0, "info": 0}


def test_empty_summary_severity_counts_all_zero() -> None:
    summary = _empty_overlay_summary(parse_error=None, parsed=False, configuration=CONFIGURATION)
    assert summary.severity_counts == dict.fromkeys(severity_keys(CONFIGURATION), 0)
