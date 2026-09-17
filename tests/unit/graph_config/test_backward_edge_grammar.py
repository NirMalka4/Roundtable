"""Tests for the reserved backward-edge grammar (contract only).

Proves a ``backward: true`` edge is parsed, round-trips through the serializer,
is doctor-validated for well-formedness, yet stays INERT under the DAG executor:
excluded from forward dependency, cycle-detection and scheduling. Declaring a
revise loop (A→B forward, B→A backward) must NOT read as a DAG cycle.
"""

from __future__ import annotations

import pytest

from roundtable.engine.executor import DagExecutor
from roundtable.graph import model as wc
from roundtable.graph.loader import _edge_from_dict, _edge_to_dict


def _revise_graph() -> tuple[wc.GraphEntry, ...]:
    return (
        wc.GraphEntry(key="A", prompt_path="a.md", edges=(wc.Edge("B"),), emoji="x"),
        wc.GraphEntry(
            key="B",
            prompt_path="b.md",
            edges=(wc.Edge(source="A", backward=True, budget=2),),
            emoji="y",
        ),
    )


def test_backward_edge_excluded_from_forward_deps():
    _a, b = _revise_graph()
    assert b.dep_keys == ()  # backward edge is not a forward dependency
    assert b.required_dep_keys == ()
    assert b.forward_edges == ()


def test_revise_loop_is_not_a_dag_cycle():
    graph = _revise_graph()
    wc.validate_graph_config(graph)  # structurally fine
    assert DagExecutor().validate(graph) == []  # A→B forward, B→A backward: acyclic


def test_backward_edge_round_trips():
    edge = wc.Edge(source="A", required=True, backward=True, budget=3)
    rec = _edge_to_dict(edge)
    assert rec == {"from": "A", "required": True, "backward": True, "budget": 3}
    assert _edge_from_dict(rec) == edge


def test_plain_edge_omits_backward_and_budget():
    assert _edge_to_dict(wc.Edge("A")) == {"from": "A", "required": True}


def test_budget_without_backward_is_rejected():
    bad = (
        wc.GraphEntry(key="A", prompt_path="a.md", edges=(), emoji="x"),
        wc.GraphEntry(
            key="B",
            prompt_path="b.md",
            edges=(wc.Edge(source="A", budget=2),),
            emoji="y",
        ),
    )
    with pytest.raises(ValueError, match="budget: without backward"):
        wc.validate_graph_config(bad)


def test_backward_edge_budget_must_be_positive():
    bad = (
        wc.GraphEntry(key="A", prompt_path="a.md", edges=(), emoji="x"),
        wc.GraphEntry(
            key="B",
            prompt_path="b.md",
            edges=(wc.Edge(source="A", backward=True, budget=0),),
            emoji="y",
        ),
    )
    with pytest.raises(ValueError, match="budget must be >= 1"):
        wc.validate_graph_config(bad)


def test_backward_edge_source_existence_still_checked():
    bad = (
        wc.GraphEntry(
            key="B",
            prompt_path="b.md",
            edges=(wc.Edge(source="GHOST", backward=True),),
            emoji="y",
        ),
    )
    with pytest.raises(ValueError, match='edge "GHOST" not found'):
        wc.validate_graph_config(bad)
