"""Unit tests for the deterministic-node runtime in the DAG scheduler (S1).

A ``runtime: deterministic`` node runs a registered pure fn over the snapshot
instead of spawning Copilot: no OVG retry loop, and every failure mode degrades
gracefully into an *invalid* ``AgentRunOutcome`` rather than crashing the run.
"""

from __future__ import annotations

import pytest

import roundtable.context.enrichers as en
from roundtable.bundle import resolve_bundle
from roundtable.graph.model import Edge, FanOut, GraphEntry, get_configuration
from roundtable.graph.predicates import parse_predicate
from tests.support.engine import run_graph_dag

_CONFIG = get_configuration(resolve_bundle("inspectorx"))


@pytest.fixture
def clean_registry():
    saved = dict(en._REGISTRY)
    en._REGISTRY.clear()
    yield en
    en._REGISTRY.clear()
    en._REGISTRY.update(saved)


def _det(key, fn_name, *, hard_deps=()):
    return GraphEntry(
        key=key,
        prompt_path="",
        edges=tuple(Edge(d) for d in hard_deps),
        emoji="x",
        kind="code",
        code_fn=fn_name,
    )


def _run(entries):
    # run_fn must never be invoked for deterministic-only graphs.
    def run_fn(**kw):  # pragma: no cover - asserted unused
        raise AssertionError("run_fn should not be called for a deterministic node")

    return run_graph_dag(
        configuration=_CONFIG,
        run_fn=run_fn,
        entries=list(entries),
        concurrency=2,
    )


def test_deterministic_node_runs_registered_fn(clean_registry):
    calls = {}

    def fn(snapshot, inputs, entry):
        calls["entry"] = entry.key
        return "ENRICHED BODY"

    clean_registry.register_enricher("ok", fn)
    res = _run([_det("Enricher1", "ok")])

    out = res.results["Enricher1"]
    assert out.valid is True
    assert out.gate is None
    assert out.response == "ENRICHED BODY"
    assert out.attempts == 1
    assert calls["entry"] == "Enricher1"


def test_deterministic_node_sees_upstream_snapshot(clean_registry):
    clean_registry.register_enricher("prod", lambda s, i, e: "UPSTREAM")

    def consumer(snapshot, inputs, entry):
        return f"saw:{snapshot['Prod'].response}"

    clean_registry.register_enricher("cons", consumer)
    res = _run([_det("Prod", "prod"), _det("Cons", "cons", hard_deps=("Prod",))])

    assert res.results["Cons"].response == "saw:UPSTREAM"
    # Producer precedes consumer in the derived execution order.
    order = res.execution_order
    assert order.index("Prod") < order.index("Cons")


def test_unregistered_fn_is_invalid_not_crash(clean_registry):
    res = _run([_det("Enricher1", "missing")])
    out = res.results["Enricher1"]
    assert out.valid is False
    assert out.gate == "deterministic-unregistered"
    assert "missing" in out.errors[0]


def test_raising_fn_degrades_gracefully(clean_registry):
    def boom(snapshot, inputs, entry):
        raise RuntimeError("kaboom")

    clean_registry.register_enricher("boom", boom)
    res = _run([_det("Enricher1", "boom")])
    out = res.results["Enricher1"]
    assert out.valid is False
    assert out.gate == "deterministic-error"
    assert "RuntimeError: kaboom" in out.errors[0]


def test_degraded_hard_dep_makes_node_invalid_without_calling_fn(clean_registry):
    # Upstream is itself an unregistered deterministic node -> invalid. The
    # consumer must then be marked degraded-input and NOT invoke its fn (G1).
    called = {"n": 0}

    def fn(snapshot, inputs, entry):
        called["n"] += 1
        return "should-not-run"

    clean_registry.register_enricher("ok", fn)
    res = _run([_det("Bad", "also-missing"), _det("E", "ok", hard_deps=("Bad",))])

    assert res.results["Bad"].valid is False
    out = res.results["E"]
    assert out.valid is False
    assert out.gate == "deterministic-degraded-input"
    assert "Bad" in out.errors[0]
    assert called["n"] == 0


def test_activation_log_is_ordered_and_projects_to_results(clean_registry):
    clean_registry.register_enricher("prod", lambda s, i, e: "UP")
    clean_registry.register_enricher("cons", lambda s, i, e: "DOWN")
    res = _run([_det("Prod", "prod"), _det("Cons", "cons", hard_deps=("Prod",))])

    # One activation per launched node, ordered by the deterministic execution order.
    assert [a.node_key for a in res.activations] == res.execution_order
    assert all(a.step_index == 0 for a in res.activations)  # 1 activation/node in a DAG
    # results is a faithful node-projection of the log.
    assert res.results == {a.node_key: a.outcome for a in res.activations}
    assert res.results["Prod"].response == "UP"
    assert res.results["Cons"].response == "DOWN"


def test_unbounded_run_terminates_within_budget(clean_registry):
    clean_registry.register_enricher("prod", lambda s, i, e: "UP")
    clean_registry.register_enricher("cons", lambda s, i, e: "DOWN")
    res = _run([_det("Prod", "prod"), _det("Cons", "cons", hard_deps=("Prod",))])
    # No max_steps: the whole graph runs and the budget never truncates.
    assert res.max_steps is None
    assert res.budget_hit is False
    assert res.terminated_within_budget is True
    assert res.steps == 2


def test_step_budget_caps_launches_and_flags_truncation(clean_registry):
    clean_registry.register_enricher("prod", lambda s, i, e: "UP")
    clean_registry.register_enricher("cons", lambda s, i, e: "DOWN")

    def run_fn(**kw):  # pragma: no cover - deterministic-only graph
        raise AssertionError("run_fn should not be called")

    # Budget of 1 over a 2-node chain: only the root launches; the consumer is
    # skipped and the run is flagged as budget-truncated.
    res = run_graph_dag(
        configuration=_CONFIG,
        run_fn=run_fn,
        entries=[_det("Prod", "prod"), _det("Cons", "cons", hard_deps=("Prod",))],
        concurrency=2,
        max_steps=1,
    )
    assert res.max_steps == 1
    assert res.steps == 1
    assert res.budget_hit is True
    assert res.terminated_within_budget is False
    assert res.execution_order == ["Prod"]
    assert "Cons" not in res.results


def test_exact_fit_budget_is_not_a_truncation(clean_registry):
    clean_registry.register_enricher("prod", lambda s, i, e: "UP")
    clean_registry.register_enricher("cons", lambda s, i, e: "DOWN")
    res = run_graph_dag(
        configuration=_CONFIG,
        run_fn=lambda **kw: None,
        entries=[_det("Prod", "prod"), _det("Cons", "cons", hard_deps=("Prod",))],
        concurrency=2,
        max_steps=2,
    )
    # Budget == node count: every node runs, nothing truncated.
    assert res.steps == 2
    assert res.budget_hit is False
    assert res.terminated_within_budget is True


# ── Conditional forward edges (when:) ─────────────────────────────────────────
def _cond(key, fn_name, source, when, *, required=True):
    """A code node with one conditional edge (``when:`` over ``source``)."""
    return GraphEntry(
        key=key,
        prompt_path="",
        edges=(Edge(source=source, required=required, when=parse_predicate(when)),),
        emoji="x",
        kind="code",
        code_fn=fn_name,
    )


def test_conditional_edge_true_runs_consumer(clean_registry):
    clean_registry.register_enricher("triage", lambda s, i, e: '{"risk": "high"}')
    clean_registry.register_enricher("deep", lambda s, i, e: "DEEP")
    deep = _cond("Deep", "deep", "Triage", {"field": "risk", "equals": "high"})
    res = _run([_det("Triage", "triage"), deep])
    assert "Deep" in res.results
    assert res.results["Deep"].valid is True
    assert res.results["Deep"].response == "DEEP"
    assert "Deep" in res.execution_order


def test_conditional_edge_false_skips_consumer(clean_registry):
    clean_registry.register_enricher("triage", lambda s, i, e: '{"risk": "low"}')

    def deep_fn(s, i, e):  # pragma: no cover - must never run
        raise AssertionError("skipped consumer's fn must not be called")

    clean_registry.register_enricher("deep", deep_fn)
    deep = _cond("Deep", "deep", "Triage", {"field": "risk", "equals": "high"})
    res = _run([_det("Triage", "triage"), deep])
    # Branch not taken: Deep is skipped — no outcome, no activation.
    assert "Deep" not in res.results
    assert "Deep" not in res.execution_order
    assert res.results["Triage"].valid is True


def test_conditional_producer_without_json_output_is_false(clean_registry):
    # Producer emits non-JSON ⇒ predicate reads {} ⇒ false ⇒ consumer skipped.
    clean_registry.register_enricher("triage", lambda s, i, e: "not json")
    clean_registry.register_enricher("deep", lambda s, i, e: "DEEP")
    deep = _cond("Deep", "deep", "Triage", {"field": "risk", "equals": "high"})
    res = _run([_det("Triage", "triage"), deep])
    assert "Deep" not in res.results


def test_downstream_of_skipped_conditional_runs_degraded_not_cascade_skipped(clean_registry):
    # Triage → Deep(conditional, skipped) → Reporter(required dep Deep).
    # No cascade-skip: Reporter is still launched, but degrades (invalid) because a
    # required dep is missing — the existing graceful-degradation behavior.
    clean_registry.register_enricher("triage", lambda s, i, e: '{"risk": "low"}')
    clean_registry.register_enricher("deep", lambda s, i, e: "DEEP")
    clean_registry.register_enricher("report", lambda s, i, e: "R")
    deep = _cond("Deep", "deep", "Triage", {"field": "risk", "equals": "high"})
    reporter = _det("Reporter", "report", hard_deps=("Deep",))
    res = _run([_det("Triage", "triage"), deep, reporter])
    assert "Deep" not in res.results  # branch skipped
    assert "Reporter" in res.results  # NOT cascade-skipped
    assert res.results["Reporter"].valid is False  # degraded on the missing dep
    assert res.results["Reporter"].gate == "deterministic-degraded-input"


# ── Send bounded fan-out (kind: map) ──────────────────────────────────────────
def _map(key, source, over):
    """A ``kind: map`` node that fans out over ``over`` from ``source``."""
    return GraphEntry(
        key=key,
        prompt_path="",
        edges=(Edge(source=source, required=True),),
        emoji="x",
        kind="map",
        fan_out=FanOut(over=over),
    )


def test_map_node_contributes_n_activations_and_aggregated_projection(clean_registry):
    clean_registry.register_enricher("prod", lambda s, i, e: '{"items": ["a", "b", "c"]}')
    res = _run([_det("Prod", "prod"), _map("Fan", "Prod", "Prod.items")])

    # The log carries the fan-out: N per-item activations (step 0..N-1) + the
    # aggregated activation (step N), all under the map node's key.
    fan = [a for a in res.activations if a.node_key == "Fan"]
    assert [a.step_index for a in fan] == [0, 1, 2, 3]
    assert [a.outcome.response for a in fan[:3]] == ["a", "b", "c"]
    # results-projection (last activation per key) is the aggregated list.
    assert res.results["Fan"].valid is True
    assert res.results["Fan"].response == '["a", "b", "c"]'
    # steps counts every activation (Prod + 4 for the map node).
    assert res.steps == 5


def test_map_node_over_empty_list_has_single_aggregated_activation(clean_registry):
    clean_registry.register_enricher("prod", lambda s, i, e: '{"items": []}')
    res = _run([_det("Prod", "prod"), _map("Fan", "Prod", "Prod.items")])
    fan = [a for a in res.activations if a.node_key == "Fan"]
    assert len(fan) == 1  # no fan-out entries; just the aggregated outcome
    assert fan[0].step_index == 0
    assert res.results["Fan"].response == "[]"


def test_map_node_degraded_source_is_invalid_no_fanout(clean_registry):
    # Producer is unregistered ⇒ invalid ⇒ map degrades to a single invalid outcome.
    res = _run([_det("Prod", "missing-fn"), _map("Fan", "Prod", "Prod.items")])
    fan = [a for a in res.activations if a.node_key == "Fan"]
    assert len(fan) == 1
    assert res.results["Fan"].valid is False
    assert res.results["Fan"].gate == "map-degraded-source"
