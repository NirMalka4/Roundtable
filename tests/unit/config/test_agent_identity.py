"""Unit tests for graph-derived agent identity (config.agent_identity)."""

from __future__ import annotations

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration
from roundtable.runtime.agent_identity import (
    get_agent_display_name,
    is_judge_like_agent,
    resolve_canonical_id,
    try_resolve_canonical_id,
)

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def test_resolves_graph_key_form():
    assert try_resolve_canonical_id("DeterministicPreScan", CONFIGURATION) == "deterministicprescan"
    assert try_resolve_canonical_id("Profiler_CodeMap", CONFIGURATION) == "profiler_codemap"
    assert try_resolve_canonical_id("SchemaDrift", CONFIGURATION) == "schema_drift"
    assert try_resolve_canonical_id("AttackSurfaceScanner", CONFIGURATION) == "attacksurface"


def test_resolves_display_name_and_canonical_forms():
    assert try_resolve_canonical_id("Profiler Code Map", CONFIGURATION) == "profiler_codemap"
    assert try_resolve_canonical_id("Chaos Simulator", CONFIGURATION) == "simulator_inverted"
    assert try_resolve_canonical_id("Happy Path Simulator", CONFIGURATION) == "simulator"
    assert try_resolve_canonical_id("profiler_codemap", CONFIGURATION) == "profiler_codemap"


def test_case_insensitive():
    assert try_resolve_canonical_id("schemadrift", CONFIGURATION) == "schema_drift"
    assert try_resolve_canonical_id("JUDGE", CONFIGURATION) == "judge"


def test_unknown_returns_none_and_resolve_raises():
    assert try_resolve_canonical_id("NotARealAgent", CONFIGURATION) is None
    try:
        resolve_canonical_id("NotARealAgent", CONFIGURATION)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Unknown agent" in str(e)


def test_is_judge_like():
    assert is_judge_like_agent("Judge", CONFIGURATION) is True
    assert is_judge_like_agent("judge", CONFIGURATION) is True
    assert is_judge_like_agent("Judge_RawFollowup", CONFIGURATION) is True
    assert is_judge_like_agent("Security", CONFIGURATION) is False
    assert is_judge_like_agent("", CONFIGURATION) is False


def test_agent_display_names_match_report_labels():
    assert get_agent_display_name("CodeCorrectness", CONFIGURATION) == "Code Correctness"
    assert get_agent_display_name("SeverityInflator", CONFIGURATION) == "Severity Review"
    assert get_agent_display_name("Simulator_inverted", CONFIGURATION) == "Chaos Simulator"
    assert get_agent_display_name("Simulator", CONFIGURATION) == "Happy Path Simulator"


def test_agent_display_name_unknown_key_falls_back_to_despaced():
    assert get_agent_display_name("Some_Unknown_Agent", CONFIGURATION) == "Some Unknown Agent"
    assert get_agent_display_name("Plain", CONFIGURATION) == "Plain"


def test_registry_derives_from_graph_non_source_entries():
    from roundtable.runtime.agent_identity import get_identity_registry

    reg_keys = {it.key for it in get_identity_registry(CONFIGURATION)}
    non_source = {e.key for e in CONFIGURATION.entries if e.kind != "source"}
    assert reg_keys == non_source
