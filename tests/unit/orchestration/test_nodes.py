"""Unit tests for the node-as-callable seam (``orchestration/nodes.py``).

Every graph node runs through :func:`dispatch_node`, which selects a per-``kind``
handler from :data:`NODE_HANDLERS`. These tests lock the seam contract directly
(the scheduler tests exercise it end-to-end); the unknown-kind guard is the one
branch not reachable through the doctor-validated graph.
"""

from __future__ import annotations

import pytest

import roundtable.context.enrichers as en
from roundtable.backend import KeywordBackend
from roundtable.bundle import resolve_bundle
from roundtable.engine import default_agent_input
from roundtable.engine.agent_runner import DEFAULT_AGENT_TIMEOUT_S, AgentRunOutcome
from roundtable.engine.nodes import (
    NODE_HANDLERS,
    NodeContext,
    NodeKind,
    dispatch_node,
    get_node_handler,
    run_source_node,
)
from roundtable.graph.model import Edge, FanOut, GraphEntry, get_configuration

_CONFIG = get_configuration(resolve_bundle("inspectorx"))


@pytest.fixture
def clean_registry():
    saved = dict(en._REGISTRY)
    en._REGISTRY.clear()
    yield en
    en._REGISTRY.clear()
    en._REGISTRY.update(saved)


def _ctx(**over) -> NodeContext:
    base = {
        "backend": KeywordBackend(lambda **_kw: None),
        "configuration": _CONFIG,
        "add_dirs": None,
        "cwd": None,
        "source_payloads": {},
        "session_reuse": True,
        "max_attempts": 1,
        "depth": {},
        "scheduled": frozenset(),
        "agent_input_builder": default_agent_input,
    }
    base.update(over)
    return NodeContext(**base)


def _entry(
    key: str, kind: str, *, code_fn: str = "", edges=(), fan_out: str | None = None
) -> GraphEntry:
    return GraphEntry(
        key=key,
        prompt_path="",
        edges=tuple(Edge(d) for d in edges),
        emoji="x",
        kind=kind,
        code_fn=code_fn,
        fan_out=FanOut(over=fan_out) if fan_out else None,
    )


def _valid(agent: str, response: str) -> AgentRunOutcome:
    return AgentRunOutcome(agent=agent, response=response, valid=True, gate=None, attempts=1)


def test_registry_holds_llm_code_reducer_and_map_handlers() -> None:
    assert set(NODE_HANDLERS) == {"llm", "code", "reducer", "map", "source"}
    assert get_node_handler("llm") is not None
    assert get_node_handler("code") is not None
    assert get_node_handler("reducer") is not None
    assert get_node_handler("map") is not None
    assert get_node_handler("nope") is None


def test_graph_kind_validation_derives_from_node_registry(monkeypatch) -> None:
    from roundtable.graph import validate_graph_config

    monkeypatch.setitem(
        NODE_HANDLERS,
        "custom",
        NodeKind(run_source_node, lambda _entry, _enrichers: []),
    )
    validate_graph_config((_entry("Custom", "custom"),))


def test_dispatch_routes_code_node_to_enricher(clean_registry) -> None:
    clean_registry.register_enricher("ok", lambda s, i, e: "BODY")
    key, outcome, _meta = dispatch_node(_entry("Enricher", "code", code_fn="ok"), {}, (), _ctx())
    assert key == "Enricher"
    assert outcome.valid is True
    assert outcome.response == "BODY"


def test_code_node_with_degraded_dep_is_invalidated(clean_registry) -> None:
    """A ``code`` node whose hard-dep input degraded is invalid — the fn is not called."""
    called = []
    clean_registry.register_enricher("ok", lambda s, i, e: called.append(1) or "BODY")
    _key, outcome, _meta = dispatch_node(
        _entry("Enricher", "code", code_fn="ok"), {}, ("MissingDep",), _ctx()
    )
    assert outcome.valid is False
    assert outcome.gate == "deterministic-degraded-input"
    assert called == []  # fn never invoked


def test_reducer_node_tolerates_degraded_dep(clean_registry) -> None:
    """A ``reducer`` merges its survivors: a degraded input still runs the fn and the
    node stays valid."""
    called = []
    clean_registry.register_enricher("merge", lambda s, i, e: called.append(1) or "MERGED")
    _key, outcome, _meta = dispatch_node(
        _entry("Reducer", "reducer", code_fn="merge"), {}, ("MissingDep",), _ctx()
    )
    assert outcome.valid is True
    assert outcome.response == "MERGED"
    assert called == [1]  # fn invoked on the survivors


def test_dispatch_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="no node handler registered for kind 'bogus'"):
        dispatch_node(_entry("Nope", "bogus"), {}, (), _ctx())


def test_map_node_fans_out_over_list_field() -> None:
    """A ``map`` node emits one sub-activation per list element plus one aggregated
    outcome carrying the whole list; snapshot exposes only the aggregated body."""
    snapshot = {"Producer": _valid("Producer", '{"items": ["a", "b", "c"]}')}
    entry = _entry("Fanner", "map", edges=["Producer"], fan_out="Producer.items")
    key, outcome, meta = dispatch_node(entry, snapshot, (), _ctx())
    assert key == "Fanner"
    assert outcome.valid is True
    subs = meta["fan_out_activations"]
    assert [s.response for s in subs] == ["a", "b", "c"]
    assert all(s.valid for s in subs)
    assert [s.agent for s in subs] == ["Fanner[0]", "Fanner[1]", "Fanner[2]"]
    assert outcome.response == '["a", "b", "c"]'  # aggregated list, delivered downstream


def test_map_node_serializes_non_string_items() -> None:
    snapshot = {"P": _valid("P", '{"rows": [{"id": 2}, {"id": 1}]}')}
    entry = _entry("M", "map", edges=["P"], fan_out="P.rows")
    _key, _outcome, meta = dispatch_node(entry, snapshot, (), _ctx())
    # dict items are json.dumps'd with sorted keys (stable, machine-readable)
    assert [s.response for s in meta["fan_out_activations"]] == ['{"id": 2}', '{"id": 1}']


def test_map_node_empty_list_is_valid_with_no_sub_activations() -> None:
    snapshot = {"P": _valid("P", '{"items": []}')}
    entry = _entry("M", "map", edges=["P"], fan_out="P.items")
    _key, outcome, meta = dispatch_node(entry, snapshot, (), _ctx())
    assert outcome.valid is True
    assert meta["fan_out_activations"] == []
    assert outcome.response == "[]"


def test_map_node_resolves_nested_field_path() -> None:
    snapshot = {"P": _valid("P", '{"meta": {"files": ["x", "y"]}}')}
    entry = _entry("M", "map", edges=["P"], fan_out="P.meta.files")
    _key, outcome, meta = dispatch_node(entry, snapshot, (), _ctx())
    assert outcome.valid is True
    assert [s.response for s in meta["fan_out_activations"]] == ["x", "y"]


def test_map_node_missing_source_is_invalid() -> None:
    entry = _entry("M", "map", edges=["P"], fan_out="P.items")
    _key, outcome, meta = dispatch_node(entry, {}, (), _ctx())
    assert outcome.valid is False
    assert outcome.gate == "map-degraded-source"
    assert meta["fan_out_activations"] == []


def test_map_node_invalid_source_is_invalid() -> None:
    snapshot = {"P": AgentRunOutcome(agent="P", response="", valid=False, gate="x", attempts=1)}
    entry = _entry("M", "map", edges=["P"], fan_out="P.items")
    _key, outcome, _meta = dispatch_node(entry, snapshot, (), _ctx())
    assert outcome.valid is False
    assert outcome.gate == "map-degraded-source"


def test_map_node_non_list_field_is_invalid() -> None:
    snapshot = {"P": _valid("P", '{"items": "not-a-list"}')}
    entry = _entry("M", "map", edges=["P"], fan_out="P.items")
    _key, outcome, _meta = dispatch_node(entry, snapshot, (), _ctx())
    assert outcome.valid is False
    assert outcome.gate == "map-invalid-source"


def test_map_node_unparseable_source_is_invalid() -> None:
    snapshot = {"P": _valid("P", "this is not json")}
    entry = _entry("M", "map", edges=["P"], fan_out="P.items")
    _key, outcome, _meta = dispatch_node(entry, snapshot, (), _ctx())
    assert outcome.valid is False
    assert outcome.gate == "map-invalid-source"


# ── timeout_seconds: the per-agent wall-clock budget must reach the runner ────
# Regression guard: the timeout was parsed into ``GraphEntry`` but no caller ever
# passed it on, so every agent silently ran on the runner's 600 s default and a
# declared 30-minute budget was inert. Assert at the seam that actually carries it.


def _llm_entry(key: str, *, timeout_seconds: int | None) -> GraphEntry:
    return GraphEntry(
        key=key,
        prompt_path="",
        edges=(),
        emoji="x",
        kind="llm",
        timeout_seconds=timeout_seconds,
    )


def _capture_timeout(monkeypatch) -> dict:
    seen: dict = {}

    def fake_run(**kw):
        seen.update(kw)
        return AgentRunOutcome(agent=kw["agent"], response="{}", valid=False, gate="g", attempts=1)

    monkeypatch.setattr("roundtable.engine.nodes.run_agent_with_ovg", fake_run)
    return seen


def test_llm_node_passes_declared_timeout_seconds_to_runner(monkeypatch) -> None:
    seen = _capture_timeout(monkeypatch)
    dispatch_node(_llm_entry("A", timeout_seconds=720), {}, (), _ctx())
    assert seen["timeout_s"] == 720.0


def test_llm_node_falls_back_to_default_timeout(monkeypatch) -> None:
    seen = _capture_timeout(monkeypatch)
    dispatch_node(_llm_entry("A", timeout_seconds=None), {}, (), _ctx())
    assert seen["timeout_s"] == DEFAULT_AGENT_TIMEOUT_S
