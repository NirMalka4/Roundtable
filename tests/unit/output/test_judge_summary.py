"""Unit tests for judge_summary + verdict exit-code mapping."""

from __future__ import annotations

import json

from roundtable.bundle import resolve_bundle
from roundtable.configs.inspectorx.plugins.verdict import (
    APPROVE,
    APPROVE_WITH_SUGGESTIONS,
    REJECT,
    UNKNOWN,
    compute_overlay_severity_counts,
    get_verdict_icon,
    parse_judge_summary,
    to_canonical_verdict,
    to_severity_bucket,
)
from roundtable.decision.verdict import (
    EXIT_CLEAN,
    EXIT_FINDINGS,
    verdict_to_exit_code,
)
from roundtable.graph import Configuration, get_configuration

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def test_to_canonical_verdict_exact() -> None:
    assert to_canonical_verdict("APPROVE") == APPROVE
    assert to_canonical_verdict("reject") == REJECT
    assert to_canonical_verdict("  Approve_With_Suggestions  ") == APPROVE_WITH_SUGGESTIONS


def test_to_canonical_verdict_fuzzy() -> None:
    # Substring containing APPROVE_WITH_SUGGESTIONS wins over plain APPROVE.
    assert to_canonical_verdict("VERDICT: APPROVE_WITH_SUGGESTIONS now") == APPROVE_WITH_SUGGESTIONS
    assert to_canonical_verdict("I approve this") == APPROVE
    # APPROVE + REJECT both present ⇒ ambiguous ⇒ UNKNOWN.
    assert to_canonical_verdict("approve or reject") == UNKNOWN
    assert to_canonical_verdict(None) == UNKNOWN
    assert to_canonical_verdict(123) == UNKNOWN
    assert to_canonical_verdict("gibberish") == UNKNOWN


def test_verdict_icons() -> None:
    assert get_verdict_icon(APPROVE) == "\u2705"
    assert get_verdict_icon(APPROVE_WITH_SUGGESTIONS) == "\u26a0\ufe0f"
    assert get_verdict_icon(REJECT) == "\u274c"
    assert get_verdict_icon(UNKNOWN) == "\U0001f534"


def test_to_severity_bucket() -> None:
    assert to_severity_bucket("CRITICAL") == "critical"
    assert to_severity_bucket("High") == "high"
    assert to_severity_bucket("blocking") == "high"  # 'blocking' maps to high
    assert to_severity_bucket("warning") == "medium"  # 'warning' maps to medium
    assert to_severity_bucket("low") == "low"
    assert to_severity_bucket("informational") == "info"
    assert to_severity_bucket("nonsense") == "info"


def test_compute_overlay_severity_counts_reads_canonical_only() -> None:
    overlay = [
        {"verdict_severity": "critical"},
        {"verdictSeverity": "high"},  # camelCase ⇒ ignored (no alias)
        {"verdict_severity": None, "verdictSeverity": "low"},  # camelCase ⇒ ignored
        {},  # no severity ⇒ contributes nothing
        {"verdict_severity": 42},  # non-string ⇒ skipped
    ]
    counts = compute_overlay_severity_counts(overlay, CONFIGURATION)
    assert counts == {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0}


def test_severity_handling_is_config_agnostic_without_critical(tmp_path) -> None:
    """A config whose severity ladder omits 'critical' (e.g. low..high) must not
    KeyError: out-of-ladder buckets are dropped and the critical auto-override no-ops."""
    import roundtable.configs.inspectorx.plugins.verdict as js

    configuration = Configuration.from_document(
        {
            "domain_values": {
                "values": {
                    "severity": ["low", "medium", "high"],
                    "verdict": ["APPROVE_WITH_SUGGESTIONS", "REJECT"],
                }
            },
            "agents": [{"key": "Input", "kind": "source", "emoji": "I"}],
        },
        root=tmp_path,
    )

    # A stray 'critical' bucket is simply not counted (not in this ladder).
    counts = js.compute_overlay_severity_counts(
        [{"verdict_severity": "critical"}, {"verdict_severity": "high"}],
        configuration,
    )
    assert counts == {"high": 1, "medium": 0, "low": 0}

    # parse_judge_summary must not raise, and must not force REJECT (no critical tier).
    s = js.parse_judge_summary(
        json.dumps(
            {
                "verdict": "APPROVE_WITH_SUGGESTIONS",
                "verdict_overlay": [
                    {"blocking": False, "verdict_severity": "high"},
                ],
            }
        ),
        configuration,
    )
    assert s.verdict == APPROVE_WITH_SUGGESTIONS
    assert s.verdict_overridden is False


def test_parse_judge_summary_counts_blocking_and_merged() -> None:
    raw = json.dumps(
        {
            "verdict": "REJECT",
            "verdict_overlay": [
                {"blocking": True, "merged_with": ["a", "b"]},
                {"blocking": False},
                {"blocking": True},
            ],
            "validated_safe": [{"source_agent": "X", "finding_id": "1"}],
            "needs_human_judgment": [{}],
            "judge_observations": [{}, {}],
        }
    )
    s = parse_judge_summary(raw, CONFIGURATION)
    assert s.parsed is True
    assert s.verdict == REJECT
    assert s.blocking_count == 2
    assert s.non_blocking_count == 1
    assert s.merged_subordinate_count == 2
    assert s.published_primary_count == 3
    assert s.safe_count == 1
    assert s.needs_human_judgment_count == 1
    assert s.judge_observations_count == 2


def test_parse_judge_summary_empty_input() -> None:
    s = parse_judge_summary("", CONFIGURATION)
    assert s.parsed is False
    assert s.verdict == UNKNOWN


def test_parse_judge_summary_array_is_not_object() -> None:
    # A JSON array has no verdict keys ⇒ treated as parse-fail ⇒ UNKNOWN.
    s = parse_judge_summary("[1, 2, 3]", CONFIGURATION)
    assert s.parsed is False
    assert s.verdict == UNKNOWN


def test_verdict_to_exit_code() -> None:
    assert verdict_to_exit_code(APPROVE) == EXIT_CLEAN
    assert verdict_to_exit_code(APPROVE_WITH_SUGGESTIONS) == EXIT_FINDINGS
    assert verdict_to_exit_code(REJECT) == EXIT_FINDINGS
    assert verdict_to_exit_code(UNKNOWN) == EXIT_FINDINGS
