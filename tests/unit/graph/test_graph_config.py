"""Unit gate for the agent graph.

Protects the SINGLE SOURCE OF TRUTH against drift from the TS original. Spot-
checks the riskiest entries plus the global DAG/uniqueness invariants that
``validate_graph_config`` enforces.
"""

from __future__ import annotations

import dataclasses

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.graph import model as wc

_CONFIG = wc.get_configuration(resolve_bundle("inspectorx"))


def _validate(entries=None) -> None:
    wc.validate_graph_config(entries, config=_CONFIG)


def test_validate_graph_config_passes_on_real_config():
    # The real DAG must satisfy: every hard dep is in an earlier wave.
    _validate()


def test_derived_key_lookup_matches_entries():
    entries = _CONFIG.entries
    assert _CONFIG.by_key == {entry.key: entry for entry in entries}


def test_entries_are_frozen():
    entry = wc.get_entry("Judge", _CONFIG)
    assert entry is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.key = "mutated"  # type: ignore[misc]


def test_pre_scan_is_non_llm_with_no_prompt():
    dps = wc.get_entry("DeterministicPreScan", _CONFIG)
    assert dps is not None
    assert dps.is_llm is False
    assert dps.prompt_path == ""


def test_judge_entry_faithful():
    j = wc.get_entry("Judge", _CONFIG)
    assert j is not None
    # Judge's finding corpus now arrives via its dedicated dossier node; it keeps
    # SeverityInflator + its structural-context deps directly. The review corpus
    # arrives via an explicit ``ReviewDiff`` source edge (required, declared first).
    assert j.required_dep_keys == ("ReviewDiff", "Dossier_Judge")
    assert j.optional_dep_keys == (
        "SeverityInflator",
        "Profiler_CodeMap",
        "Profiler_Intent",
        "SecurityIntentProfiler",
        "Historian",
    )
    assert j.timeout_seconds == 30 * 60


def test_exploit_engineer_deps_faithful():
    ee = wc.get_entry("ExploitEngineer", _CONFIG)
    assert ee is not None
    # Its dossier node SUPERSEDES the raw finding-emitter edges (Security,
    # AttackSurfaceScanner, the three Analyst_*, CodeCorrectness, Deadlock, Privacy,
    # Architecture) — their findings now arrive consolidated via Dossier_ExploitEngineer.
    # Kept raw: ReviewDiff (corpus), the structural producers (Profiler_Scenarios,
    # SecurityFocusPack, PenTest), and Simulator_inverted + SecurityIntentProfiler —
    # which emit NO findings, so their raw edge is the sole delivery path.
    assert ee.required_dep_keys == (
        "ReviewDiff",
        "Simulator_inverted",
        "Profiler_Scenarios",
        "SecurityIntentProfiler",
        "SecurityFocusPack",
        "PenTest",
        "Dossier_ExploitEngineer",
    )
    assert ee.optional_dep_keys == ()


def test_optimizer_overrides_and_scope():
    opt = wc.get_entry("Optimizer", _CONFIG)
    assert opt is not None
    assert opt.optional_dep_keys == ("Profiler_CodeMap",)


def test_git_context_modes_and_repo_instructions():
    # Only Historian keeps changed-files-only (its change-evolution charter + inject_git_history
    # make hunk locations sufficient). DocsKeeper/TestQuality default to full: their charters are
    # body-level, and changed-files-only only made them reconstruct omitted bodies via extra tool
    # calls without saving tokens.
    assert wc.get_entry("Historian", _CONFIG).git_context_mode == "changed-files-only"
    assert wc.get_entry("DocsKeeper", _CONFIG).git_context_mode is None
    assert wc.get_entry("TestQuality", _CONFIG).git_context_mode is None
    # Exactly one entry injects repo instructions, and it is Analyst Pass C.
    injectors = [e.key for e in _CONFIG.entries if e.inject_repo_instructions]
    assert injectors == ["Analyst_Standards"]


def test_computed_agent_sets():
    cfg = _CONFIG
    assert (
        frozenset({"SeverityInflator", "Judge", "SuggestionPublisher", "Verdict"})
        == cfg.terminal_agents
    )
    assert frozenset({"SuggestionPublisher"}) == cfg.non_graph_infra_agents


def test_dossier_nodes_cover_all_finding_producers():
    # The per-consumer dossier nodes replace the old ``collects_all`` implicit
    # auto-include: each zero-drop node must dep on every non-terminal finding
    # producer so no specialist is silently dropped from the reconciled corpus.
    producers = set(wc.finding_producing_agent_keys(include_terminal=False, config=_CONFIG))
    for node_key in ("Dossier_Judge", "Dossier_SeverityInflator"):
        node = wc.get_entry(node_key, _CONFIG)
        assert node is not None
        scope = set(node.dep_keys)
        assert producers <= scope, f"{node_key} missing {sorted(producers - scope)}"
    # SeverityInflator is a terminal producer and must NOT be in either corpus
    # (its inflated_findings re-index the same findings — would double-count).
    for node_key in ("Dossier_Judge", "Dossier_SeverityInflator"):
        node = wc.get_entry(node_key, _CONFIG)
        scope = set(node.dep_keys)
        assert "SeverityInflator" not in scope


def test_validate_detects_dependency_cycle():
    bad = (
        wc.GraphEntry(
            key="A",
            prompt_path="a.md",
            edges=(wc.Edge("B"),),
            emoji="x",
        ),
        wc.GraphEntry(
            key="B",
            prompt_path="b.md",
            edges=(wc.Edge("A"),),
            emoji="y",
        ),
    )
    # Acyclicity is the DAG executor's concern, NOT the executor-agnostic validator:
    # validate_graph_config accepts the (structurally fine) cyclic graph …
    _validate(bad)
    # … and DagExecutor.validate is what rejects the cycle.
    from roundtable.engine.executor import DagExecutor

    errs = DagExecutor().validate(bad)
    assert any("dependency cycle" in e for e in errs)


def test_validate_rejects_when_on_optional_edge():
    from roundtable.graph.predicates import parse_predicate

    bad = (
        wc.GraphEntry(key="Prod", prompt_path="p.md", edges=(), emoji="x"),
        wc.GraphEntry(
            key="Cons",
            prompt_path="c.md",
            edges=(
                wc.Edge(
                    source="Prod",
                    required=False,
                    when=parse_predicate({"field": "risk", "equals": "high"}),
                ),
            ),
            emoji="x",
        ),
    )
    with pytest.raises(ValueError) as exc:
        _validate(bad)
    assert "when: requires required: true" in str(exc.value)


def test_validate_accepts_when_on_required_edge():
    from roundtable.graph.predicates import parse_predicate

    ok = (
        wc.GraphEntry(key="Prod", prompt_path="p.md", edges=(), emoji="x"),
        wc.GraphEntry(
            key="Cons",
            prompt_path="c.md",
            edges=(
                wc.Edge(
                    source="Prod",
                    required=True,
                    when=parse_predicate({"field": "risk", "equals": "high"}),
                ),
            ),
            emoji="x",
        ),
    )
    _validate(ok)  # no raise


def test_validate_detects_missing_dep_and_llm_without_prompt():
    bad = (
        wc.GraphEntry(
            key="A",
            prompt_path="",
            edges=(wc.Edge("MISSING"),),
            emoji="x",
        ),
    )
    with pytest.raises(ValueError) as exc:
        _validate(bad)
    msg = str(exc.value)
    assert "not found" in msg
    assert "kind=llm but no prompt_path" in msg


def test_validate_deterministic_requires_resolvable_fn_and_no_llm_fields():
    bad = (
        wc.GraphEntry(
            key="Det",
            prompt_path="det.md",  # deterministic must NOT declare a prompt
            edges=(),
            emoji="x",
            kind="code",
            code_fn="no_such_enricher",  # unresolvable
            model=("claude-opus-4.8",),  # deterministic must NOT declare a model
        ),
    )
    with pytest.raises(ValueError) as exc:
        _validate(bad)
    msg = str(exc.value)
    assert "not in the enricher registry" in msg
    assert "must not declare a prompt_path" in msg
    assert "must not declare a model" in msg


def test_validate_deterministic_requires_fn_set():
    bad = (
        wc.GraphEntry(
            key="Det",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="code",  # code_fn unset
        ),
    )
    with pytest.raises(ValueError, match="no code_fn"):
        _validate(bad)


def test_validate_reducer_shares_deterministic_fn_contract():
    """A ``reducer`` obeys the same fn-kind contract as ``code``: a resolvable
    ``code_fn`` and no LLM-only fields."""
    bad = (
        wc.GraphEntry(
            key="Red",
            prompt_path="red.md",  # reducer must NOT declare a prompt
            edges=(),
            emoji="x",
            kind="reducer",
            code_fn="no_such_enricher",  # unresolvable
            model=("claude-opus-4.8",),  # reducer must NOT declare a model
        ),
    )
    with pytest.raises(ValueError) as exc:
        _validate(bad)
    msg = str(exc.value)
    assert "not in the enricher registry" in msg
    assert "must not declare a prompt_path" in msg
    assert "must not declare a model" in msg


def test_validate_reducer_with_resolvable_fn_passes():
    ok = (
        wc.GraphEntry(
            key="Red",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="reducer",
            code_fn="run_prescan",
        ),
    )
    _validate(ok)  # no raise


def test_validate_map_requires_fan_out():
    bad = (wc.GraphEntry(key="M", prompt_path="", edges=(), emoji="x", kind="map"),)
    with pytest.raises(ValueError, match="kind=map but no fan_out spec"):
        _validate(bad)


def test_validate_map_forbids_llm_and_fn_fields():
    bad = (
        wc.GraphEntry(
            key="M",
            prompt_path="m.md",  # map must NOT declare a prompt
            edges=(),
            emoji="x",
            kind="map",
            code_fn="run_prescan",  # map v1 has no per-item fn
            model=("claude-opus-4.8",),  # map must NOT declare a model
            fan_out=wc.FanOut(over="P.items"),
        ),
    )
    with pytest.raises(ValueError) as exc:
        _validate(bad)
    msg = str(exc.value)
    assert "kind=map must not declare a prompt_path" in msg
    assert "kind=map must not declare a model" in msg
    assert "kind=map must not declare a code_fn" in msg


def test_validate_map_over_must_be_producer_dot_field():
    bad = (
        wc.GraphEntry(
            key="M",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="map",
            fan_out=wc.FanOut(over="nodot"),
        ),
    )
    with pytest.raises(ValueError, match=r"must be '<producer>\.<list_field>'"):
        _validate(bad)


def test_validate_fan_out_only_valid_on_map():
    bad = (
        wc.GraphEntry(
            key="C",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="code",
            code_fn="run_prescan",
            fan_out=wc.FanOut(over="P.items"),
        ),
    )
    with pytest.raises(ValueError, match="fan_out is only valid on kind=map"):
        _validate(bad)


def test_validate_map_with_fan_out_passes():
    ok = (
        wc.GraphEntry(
            key="M",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="map",
            fan_out=wc.FanOut(over="P.items"),
        ),
    )
    _validate(ok)  # no raise (producer↔schema coherence is Layer 10)


def _render_spec():
    from roundtable.consolidation import RenderSpec

    return RenderSpec(
        preamble="p",
        empty_note="none",
        group_prefix="C",
        item_singular="finding",
        group_plural_label="Concerns",
        item_plural_label="Findings",
    )


def test_validate_consolidate_requires_block():
    bad = (
        wc.GraphEntry(
            key="D", prompt_path="", edges=(), emoji="x", kind="reducer", code_fn="consolidate"
        ),
    )
    with pytest.raises(ValueError, match="code_fn=consolidate but no consolidation block"):
        _validate(bad)


def test_validate_consolidate_extract_must_resolve():
    bad = (
        wc.GraphEntry(
            key="D",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="reducer",
            code_fn="consolidate",
            consolidation=wc.ConsolidationSpec(
                extract="nope", adjacency_gap=10, render=_render_spec()
            ),
        ),
    )
    with pytest.raises(ValueError, match=r"consolidation.extract 'nope' not in the extractor"):
        _validate(bad)


def test_validate_consolidation_only_with_consolidate():
    bad = (
        wc.GraphEntry(
            key="D",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="reducer",
            code_fn="run_prescan",
            consolidation=wc.ConsolidationSpec(
                extract="specialist_findings", adjacency_gap=10, render=_render_spec()
            ),
        ),
    )
    with pytest.raises(ValueError, match="consolidation is only valid with code_fn=consolidate"):
        _validate(bad)


def test_validate_consolidate_with_registered_extract_passes():
    ok = (
        wc.GraphEntry(
            key="D",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="reducer",
            code_fn="consolidate",
            consolidation=wc.ConsolidationSpec(
                extract="specialist_findings", adjacency_gap=10, render=_render_spec()
            ),
        ),
    )
    _validate(ok)  # no raise


def test_validate_rejects_unknown_kind():
    bad = (wc.GraphEntry(key="X", prompt_path="", edges=(), emoji="x", kind="bogus"),)
    with pytest.raises(ValueError, match="invalid kind 'bogus'"):
        _validate(bad)


def test_validate_llm_must_not_declare_deterministic_fn():
    bad = (
        wc.GraphEntry(
            key="A",
            prompt_path="a.md",
            edges=(),
            emoji="x",
            code_fn="run_prescan",  # llm must not
        ),
    )
    with pytest.raises(ValueError, match="must not declare a code_fn"):
        _validate(bad)


def test_validate_non_llm_must_not_declare_tool_policy():
    policy = wc.ToolPolicy(powershell=wc.PowershellToolPolicy(120, False))
    bad = (
        wc.GraphEntry(
            key="A",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="source",
            tool_policy=policy,
        ),
    )
    with pytest.raises(ValueError, match="tool_policy is only valid on kind=llm"):
        _validate(bad)


def test_validate_detects_delivery_label_collision():
    bad = (
        wc.GraphEntry(
            key="A",
            prompt_path="a.md",
            edges=(),
            emoji="x",
            delivery_label="## shared",
        ),
        wc.GraphEntry(
            key="B",
            prompt_path="b.md",
            edges=(),
            emoji="y",
            delivery_label="## shared",
        ),
    )
    with pytest.raises(ValueError, match="collides with"):
        _validate(bad)


def test_real_config_exposes_inspectorx_branding():
    # The InspectorX bundle names the review PRODUCT; the engine name is not used.
    cfg = _CONFIG
    assert cfg.product_name == "InspectorX"
    assert cfg.report_title == "Deep Compute Review"
    assert cfg.product_emoji == "\N{SLEUTH OR SPY}\N{VARIATION SELECTOR-16}"
    assert wc.get_product_name(_CONFIG.root) == "InspectorX"
    assert wc.get_product_emoji(_CONFIG.root) == "\N{SLEUTH OR SPY}\N{VARIATION SELECTOR-16}"
    assert wc.get_report_title(_CONFIG.root) == "Deep Compute Review"


def test_branding_defaults_to_engine_name_when_absent(tmp_path):
    # A config with no branding block speaks as the ENGINE (the engine/bundle split).
    from roundtable.graph.loader import (
        load_product_emoji,
        load_product_name,
        load_report_title,
    )
    from roundtable.runtime.branding import APP_NAME

    p = tmp_path / "agent_graph.yaml"
    p.write_text("agents: []\n", encoding="utf-8")
    assert load_product_name(p) == APP_NAME
    assert load_report_title(p) == "Review"
    # No glyph at all, so a surface renders the name alone rather than a stray mark.
    assert load_product_emoji(p) == ""


def test_branding_loaders_read_the_block(tmp_path):
    from roundtable.graph.loader import (
        load_product_emoji,
        load_product_name,
        load_report_title,
    )

    p = tmp_path / "agent_graph.yaml"
    p.write_text(
        "branding:\n"
        "  product_name: Acme Review\n"
        "  product_emoji: \N{ROUND PUSHPIN}\n"
        "  report_title: Nightly Audit\n"
        "agents: []\n",
        encoding="utf-8",
    )
    assert load_product_name(p) == "Acme Review"
    assert load_product_emoji(p) == "\N{ROUND PUSHPIN}"
    assert load_report_title(p) == "Nightly Audit"
