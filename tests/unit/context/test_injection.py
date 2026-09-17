"""Unit tests for the inter-agent context-injection layer."""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace

from roundtable.bundle import resolve_bundle
from roundtable.context.injection import build_injection_sections as _build_injection_sections
from roundtable.graph.model import Edge, GraphEntry, get_configuration

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
build_injection_sections = partial(_build_injection_sections, configuration=_CONFIG)


def _entry(key: str, *, hard=(), soft=()) -> GraphEntry:
    return GraphEntry(
        key=key,
        prompt_path=f"Agents/{key}.agent.md",
        edges=tuple(Edge(d) for d in hard) + tuple(Edge(d, required=False) for d in soft),
        emoji="x",
    )


def _out(response: str, valid: bool = True) -> SimpleNamespace:
    return SimpleNamespace(response=response, valid=valid)


def test_leaf_agent_no_deps_no_flag_is_empty():
    """A profiler-like leaf agent gets no injection block (byte-identical to before)."""
    entry = _entry("Profiler_CodeMap")
    assert build_injection_sections(entry, {}, frozenset()) == ""


def test_hard_dep_valid_renders_required_context_verbatim():
    entry = _entry("Analyst_Standards", hard=("Profiler_CodeMap",))
    snap = {"Profiler_CodeMap": _out("DATA FLOW MAP\n- foo -> bar")}
    out = build_injection_sections(entry, snap, frozenset({"Profiler_CodeMap"}))
    assert out.startswith("\n\n---\n")
    assert "## Context from Profiler_CodeMap [REQUIRED]" in out
    assert "DATA FLOW MAP\n- foo -> bar" in out  # verbatim


def test_hard_dep_missing_renders_unavailable_stub():
    entry = _entry("Analyst_Standards", hard=("Profiler_CodeMap",))
    out = build_injection_sections(entry, {}, frozenset({"Profiler_CodeMap"}))
    assert "## Context from Profiler_CodeMap [REQUIRED — UNAVAILABLE]" in out
    assert "degraded" in out


def test_hard_dep_invalid_output_is_unavailable():
    entry = _entry("Analyst_Standards", hard=("Profiler_CodeMap",))
    snap = {"Profiler_CodeMap": _out("garbage", valid=False)}
    out = build_injection_sections(entry, snap, frozenset({"Profiler_CodeMap"}))
    assert "[REQUIRED — UNAVAILABLE]" in out
    assert "garbage" not in out


def test_soft_dep_valid_renders_optional_enrichment():
    entry = _entry("Simulator", hard=("Profiler_Scenarios",), soft=("Deadlock",))
    snap = {
        "Profiler_Scenarios": _out("SCENARIOS"),
        "Deadlock": _out("DEADLOCK FINDINGS"),
    }
    sched = frozenset({"Profiler_Scenarios", "Deadlock"})
    out = build_injection_sections(entry, snap, sched)
    assert "## Context from Profiler_Scenarios [REQUIRED]" in out
    assert "## Context from Deadlock [OPTIONAL]" in out
    assert "DEADLOCK FINDINGS" in out


def test_soft_dep_missing_is_silent():
    entry = _entry("Simulator", soft=("Deadlock",))
    out = build_injection_sections(entry, {}, frozenset({"Deadlock"}))
    # No required deps, missing optional dep → nothing injected.
    assert out == ""


def test_unscheduled_infra_hard_dep_is_skipped():
    """A hard dep outside the scheduled set (e.g. a ``non_graph_infra`` producer)
    has no snapshot to deliver → no section (byte-identical to a leaf)."""
    entry = _entry("CodeCorrectness", hard=("SuggestionPublisher",))
    out = build_injection_sections(entry, {}, frozenset())  # producer not scheduled
    assert out == ""


def test_source_edge_is_skipped_not_injected():
    """A ``kind: source`` edge (the review corpus) is delivered by the node handler,
    not injected here — the generic injection skips it, so it never appears as a
    ``[REQUIRED]`` / ``[REQUIRED — UNAVAILABLE]`` section."""
    entry = _entry("CodeCorrectness", hard=("ReviewDiff",))
    snap = {"ReviewDiff": _out("diff --git a/x b/x")}
    out = build_injection_sections(entry, snap, frozenset({"ReviewDiff"}))
    assert out == ""


def test_dps_delivered_via_snapshot_as_scheduled_producer():
    """DeterministicPreScan is a scheduled deterministic node; its snapshot output
    is delivered through the generic path as a [REQUIRED] section."""
    entry = _entry("CodeCorrectness", hard=("DeterministicPreScan",))
    snap = {"DeterministicPreScan": _out("Deterministic Pre-Scan: No obvious issues found.")}
    out = build_injection_sections(entry, snap, frozenset({"DeterministicPreScan"}))
    assert out.startswith("\n\n---\n")
    assert "## Context from DeterministicPreScan [REQUIRED]" in out
    assert "Deterministic Pre-Scan: No obvious issues found." in out
    assert "UNAVAILABLE" not in out  # present, not degraded


def test_dps_renders_alongside_scheduled_hard_dep():
    """A consumer hard-depping both DPS and another scheduled producer gets both
    sections, in declaration order (DPS declared first → rendered first)."""
    entry = _entry("SchemaDrift", hard=("DeterministicPreScan", "Profiler_CodeMap"))
    snap = {
        "DeterministicPreScan": _out("### SQL Helper Caller Inventory\n..."),
        "Profiler_CodeMap": _out("MAP"),
    }
    sched = frozenset({"DeterministicPreScan", "Profiler_CodeMap"})
    out = build_injection_sections(entry, snap, sched)
    assert "## Context from DeterministicPreScan [REQUIRED]" in out
    assert "## Context from Profiler_CodeMap [REQUIRED]" in out
    assert out.index("DeterministicPreScan") < out.index("Profiler_CodeMap")


def test_dps_not_injected_for_non_consumer():
    """An agent that does not hard-dep DPS never gets its section, even when DPS
    ran and is in the snapshot."""
    entry = _entry("Profiler_CodeMap")  # no DPS hard dep
    snap = {"DeterministicPreScan": _out("Deterministic Pre-Scan: No obvious issues found.")}
    out = build_injection_sections(entry, snap, frozenset({"DeterministicPreScan"}))
    assert out == ""


def test_section_separator_convention():
    """Multiple sections are joined with the standard \\n\\n---\\n separator."""
    entry = _entry("CodeCorrectness", hard=("Profiler_CodeMap", "Profiler_Intent"))
    snap = {
        "Profiler_CodeMap": _out("MAP"),
        "Profiler_Intent": _out("INTENT"),
    }
    sched = frozenset({"Profiler_CodeMap", "Profiler_Intent"})
    out = build_injection_sections(entry, snap, sched)
    # two sections → exactly two separators (leading + between).
    assert out.count("\n\n---\n") == 2


# ─── SecurityIntentProfiler relabel + security_focus_pack ───────────────────

_SIP_JSON = (
    '{"change_security_intent": "harden auth", '
    '"selected_sec_checks": ["SEC-001-SEC-015", "SEC-016-SEC-035"], '
    '"priority_scenarios": ["token replay"]}'
)


def test_security_consumer_gets_intent_pack_not_generic_header():
    """A security consumer receives SIP relabeled to ``security_intent_pack`` ONCE,
    never the generic ``## Context from SecurityIntentProfiler`` header. The derived
    ``security_focus_pack`` is a separate node (see below), not appended here."""
    entry = _entry("Security", hard=("SecurityIntentProfiler",))
    snap = {"SecurityIntentProfiler": _out(_SIP_JSON)}
    out = build_injection_sections(entry, snap, frozenset({"SecurityIntentProfiler"}))
    assert "## security_intent_pack [REQUIRED]" in out
    assert "## Context from SecurityIntentProfiler" not in out  # no double-inject
    assert _SIP_JSON in out  # verbatim SIP output, once
    assert out.count("## security_intent_pack") == 1
    # focus_pack is NOT appended by the SIP branch — it is a separate node.
    assert "## security_focus_pack" not in out


def test_security_focus_pack_node_delivered_via_generic_path():
    """SecurityFocusPack is a scheduled deterministic producer; its snapshot output
    is delivered to a consumer under the declared ``## security_focus_pack`` label."""
    entry = _entry("Security", hard=("SecurityIntentProfiler", "SecurityFocusPack"))
    snap = {
        "SecurityIntentProfiler": _out(_SIP_JSON),
        "SecurityFocusPack": _out('```json\n{"selected_sec_checks": []}\n```'),
    }
    sched = frozenset({"SecurityIntentProfiler", "SecurityFocusPack"})
    out = build_injection_sections(entry, snap, sched)
    assert "## security_intent_pack [REQUIRED]" in out
    assert "## security_focus_pack [REQUIRED]" in out
    # intent_pack (SIP dep) precedes focus_pack (declared after it).
    assert out.index("security_intent_pack") < out.index("security_focus_pack")


def test_security_focus_pack_node_unavailable_when_not_scheduled():
    """A consumer hard-depping SecurityFocusPack with no snapshot output gets the
    generic label-parametrized UNAVAILABLE stub (G1 degraded-SIP path)."""
    entry = _entry("Security", hard=("SecurityFocusPack",))
    out = build_injection_sections(entry, {}, frozenset({"SecurityFocusPack"}))
    assert "## security_focus_pack [REQUIRED — UNAVAILABLE]" in out


def test_security_consumer_sip_unavailable_emits_intent_pack_stub():
    """SIP degraded → unavailable intent_pack stub. The focus_pack node is separate;
    this consumer does not hard-dep it, so no focus_pack section here."""
    entry = _entry("PenTest", hard=("SecurityIntentProfiler",))
    snap = {"SecurityIntentProfiler": _out("", valid=False)}
    out = build_injection_sections(entry, snap, frozenset({"SecurityIntentProfiler"}))
    assert "## security_intent_pack [REQUIRED — UNAVAILABLE]" in out
    assert "## security_focus_pack" not in out


def test_non_security_hard_dep_uses_generic_header():
    """An agent that hard-deps SecurityIntentProfiler is the gate; a non-SIP dep
    still renders the generic ``## Context from <Dep>`` header (no relabel)."""
    entry = _entry("CodeCorrectness", hard=("Profiler_CodeMap",))
    snap = {"Profiler_CodeMap": _out("MAP")}
    out = build_injection_sections(entry, snap, frozenset({"Profiler_CodeMap"}))
    assert "## Context from Profiler_CodeMap [REQUIRED]" in out
    assert "security_intent_pack" not in out
    assert "security_focus_pack" not in out
