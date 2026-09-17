"""Unit tests for the Executor seam (``orchestration/executor.py``).

The DAG scheduler is exercised end-to-end elsewhere; these tests lock the seam:
``DagExecutor`` is a named :class:`Executor` and its ``run`` forwards verbatim to
``run_graph_dag`` (so callers depend on the interface, not the function).
"""

from __future__ import annotations

import pytest

import roundtable.engine.executor as ex
from roundtable.engine.executor import DagExecutor, Executor, get_executor


def test_dag_executor_satisfies_protocol() -> None:
    e = DagExecutor()
    assert e.name == "dag"
    assert isinstance(e, Executor)  # runtime_checkable structural match


def test_run_forwards_all_kwargs_to_run_graph_dag(monkeypatch) -> None:
    seen: dict = {}
    sentinel = object()

    def _fake_run_graph_dag(**kwargs):
        seen.update(kwargs)
        return sentinel

    monkeypatch.setattr(ex, "run_graph_dag", _fake_run_graph_dag)

    result = DagExecutor().run(concurrency=3, max_attempts=2)

    assert result is sentinel
    assert seen == {"concurrency": 3, "max_attempts": 2}


def test_get_executor_resolves_registered_name() -> None:
    e = get_executor("dag")
    assert isinstance(e, DagExecutor)
    assert isinstance(e, Executor)


def test_get_executor_defaults_to_dag() -> None:
    assert isinstance(get_executor(), DagExecutor)


def test_get_executor_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown executor 'nope'"):
        get_executor("nope")


def _entry(key, deps=()):
    from roundtable.graph.model import Edge, GraphEntry

    return GraphEntry(
        key=key,
        prompt_path="",
        edges=tuple(Edge(d) for d in deps),
        emoji="x",
        kind="code",
        code_fn="f",
    )


def test_dag_validate_passes_on_acyclic_graph() -> None:
    graph = (_entry("A"), _entry("B", deps=("A",)))
    assert DagExecutor().validate(graph) == []


def test_dag_validate_reports_cycle() -> None:
    graph = (_entry("A", deps=("B",)), _entry("B", deps=("A",)))
    errs = DagExecutor().validate(graph)
    assert len(errs) == 1
    assert "dependency cycle" in errs[0]


def test_dag_validate_ignores_external_deps() -> None:
    # A dep outside the graph is an always-satisfied root, not a cycle.
    graph = (_entry("A", deps=("NotInGraph",)),)
    assert DagExecutor().validate(graph) == []


def test_dag_sinks_returns_out_degree_zero_nodes() -> None:
    # A -> B -> C ; only C has no consumer.
    graph = (_entry("A"), _entry("B", deps=("A",)), _entry("C", deps=("B",)))
    assert DagExecutor().sinks(graph) == ["C"]


def test_dag_sinks_returns_multiple_leaves() -> None:
    # A fans out to B and C; both are leaves (returned sorted).
    graph = (_entry("A"), _entry("B", deps=("A",)), _entry("C", deps=("A",)))
    assert DagExecutor().sinks(graph) == ["B", "C"]


def test_dag_sinks_ignores_edges_outside_the_set() -> None:
    # An edge to a node outside ``entries`` must not mark the source as consumed.
    graph = (_entry("A", deps=("External",)),)
    assert DagExecutor().sinks(graph) == ["A"]
